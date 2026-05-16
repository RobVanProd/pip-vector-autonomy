from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse

from .audio_capture import AudioCapture
from .autonomy import AutonomyLoop
from .conversation_session import ConversationSession
from .dashboard import DASHBOARD_HTML
from .emotion import EmotionEngine
from .environment_map import load_environment_map, record_environment_observation
from .events import add_event, list_events
from .external_camera import (
    LATEST_FILENAME as EXTERNAL_CAMERA_LATEST_FILENAME,
    capture_external_view,
    external_camera_status,
    get_latest_external_view,
)
from .executors import get_executor
from .goals import GoalEngine
from .listener import Listener
from .memory import add_fact, load_memory, remember_turn
from .openai_proxy import chat_completion
from .ollama_runtime import ollama_generate
from .planner import create_conversation_plan, create_plan
from .robot_io import (
    capture_and_describe_view,
    get_cached_robot_snapshot,
    get_latest_vision,
    read_robot_snapshot,
    snapshot_to_robot_state,
)
from .schemas import (
    AutonomyConfig,
    AutonomyStatus,
    Action,
    BehaviorAction,
    ChatRequest,
    ConversationSessionConfig,
    ConversationSessionStatus,
    DriveAction,
    ExecuteRequest,
    ExecuteResponse,
    HeadAction,
    ListenerConfig,
    ListenerStatus,
    LiftAction,
    MemoryFactRequest,
    PlanExecuteRequest,
    PlanRequest,
    PlanResponse,
    RobotState,
    SentinelConfig,
    SentinelStatus,
    StopAction,
    TurnAction,
    VisionConfig,
    VisionStatus,
    VoiceBridgeConfig,
    VoiceBridgeStatus,
    WirePodTranscriptRequest,
)
from .sentinel import Sentinel
from .vision_loop import VisionLoop
from .voice_bridge import VoiceBridge

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
MODEL = os.getenv("VECTOR_BRAIN_MODEL", "gemma4:e4b")
EXECUTION_MODE = os.getenv("VECTOR_EXECUTION_MODE", "mock")
VECTOR_SERIAL = os.getenv("VECTOR_SERIAL")
VISION_MODEL = os.getenv("VECTOR_VISION_MODEL", "moondream:latest,llava:7b")
EXTERNAL_CAMERA_VISION_MODEL = os.getenv("VECTOR_EXTERNAL_CAMERA_VISION_MODEL", "llava:7b,moondream:latest")
MAP_VISION_MODEL = os.getenv("VECTOR_MAP_VISION_MODEL", "moondream:latest")
CAPTURE_DIR = Path(os.getenv("VECTOR_CAPTURE_DIR", "captures")).resolve()

app = FastAPI(title="Vector Brain", version="0.3.0")

# ── Shared inner-life engines (singleton — shared by autonomy + sentinel) ─────
emotion_engine = EmotionEngine()
goal_engine = GoalEngine()

autonomy = AutonomyLoop(
    model=MODEL,
    ollama_base_url=OLLAMA_BASE_URL,
    execution_mode=EXECUTION_MODE,
    vector_serial=VECTOR_SERIAL,
    vision_model=VISION_MODEL,
    capture_dir=CAPTURE_DIR,
    emotion_engine=emotion_engine,
    goal_engine=goal_engine,
)
sentinel = Sentinel(
    model=MODEL,
    ollama_base_url=OLLAMA_BASE_URL,
    execution_mode=EXECUTION_MODE,
    vector_serial=VECTOR_SERIAL,
    emotion_engine=emotion_engine,
    goal_engine=goal_engine,
)
voice_bridge = VoiceBridge(
    model=MODEL,
    ollama_base_url=OLLAMA_BASE_URL,
    execution_mode=EXECUTION_MODE,
    vector_serial=VECTOR_SERIAL,
)
vision_loop = VisionLoop(
    vector_serial=VECTOR_SERIAL,
    ollama_base_url=OLLAMA_BASE_URL,
    vision_model=VISION_MODEL,
    capture_dir=CAPTURE_DIR,
)
audio_capture = AudioCapture(serial=VECTOR_SERIAL)
listener = Listener(audio_capture=audio_capture, capture_dir=CAPTURE_DIR)
conversation_session = ConversationSession(
    model=MODEL,
    ollama_base_url=OLLAMA_BASE_URL,
    execution_mode=EXECUTION_MODE,
    vector_serial=VECTOR_SERIAL,
    emotion_engine=emotion_engine,
    goal_engine=goal_engine,
    listener=listener,
)
background_tasks: set[asyncio.Task] = set()
_llm_warmup_task: asyncio.Task | None = None
_last_llm_warmup_at = 0.0


@app.get("/health")
async def health():
    return {
        "ok": True,
        "model": MODEL,
        "ollama": OLLAMA_BASE_URL,
        "execution_mode": EXECUTION_MODE,
        "vector_serial": VECTOR_SERIAL,
        "vision_model": VISION_MODEL,
        "external_camera_vision_model": EXTERNAL_CAMERA_VISION_MODEL,
        "map_vision_model": MAP_VISION_MODEL,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_alias():
    return DASHBOARD_HTML


@app.get("/events")
async def events(limit: int = 100):
    return {"events": list_events(limit)}


@app.get("/memory")
async def memory():
    return load_memory()


@app.post("/memory/facts")
async def memory_fact(req: MemoryFactRequest):
    data = add_fact(req.fact)
    add_event("memory", {"fact": req.fact, "memory": data})
    return data


@app.post("/v1/chat/completions")
async def openai_chat_completions(payload: dict):
    return await chat_completion(payload, OLLAMA_BASE_URL, MODEL)


@app.post("/llm/warmup")
async def llm_warmup(model: str | None = None):
    return await _warm_llm(model=model)


async def _warm_llm(model: str | None = None) -> dict:
    global _last_llm_warmup_at
    target_model = model or MODEL
    started = time.perf_counter()
    payload = {
        "model": target_model,
        "prompt": "Return exactly: ok",
        "stream": False,
        "keep_alive": os.getenv("VECTOR_OLLAMA_KEEP_ALIVE", "15m"),
        "options": {"temperature": 0.0, "num_predict": 4},
    }
    data = await ollama_generate(OLLAMA_BASE_URL, payload, timeout=180)
    result = {
        "ok": True,
        "model": target_model,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "response": (data.get("response") or "").strip(),
        "ollama": {key: data.get(key) for key in (
            "total_duration",
            "load_duration",
            "prompt_eval_count",
            "prompt_eval_duration",
            "eval_count",
            "eval_duration",
        ) if data.get(key) is not None},
    }
    add_event("llm_warmup", result)
    if target_model == MODEL:
        _last_llm_warmup_at = time.monotonic()
    return result


def _schedule_llm_warmup(reason: str) -> None:
    global _llm_warmup_task, _last_llm_warmup_at
    now = time.monotonic()
    if _llm_warmup_task is not None and not _llm_warmup_task.done():
        add_event("llm_rewarm_skipped", {"reason": reason, "why": "warmup already running"})
        return
    if now - _last_llm_warmup_at < 8.0:
        add_event("llm_rewarm_skipped", {"reason": reason, "why": "recent warmup"})
        return

    async def warm() -> None:
        try:
            result = await _warm_llm()
            add_event("llm_rewarm", {"reason": reason, "result": result})
        except Exception as exc:
            add_event("llm_rewarm_error", {"reason": reason, "error": str(exc)})

    _llm_warmup_task = _start_background_task(warm())


@app.post("/diagnostics/latency-sample")
async def diagnostics_latency_sample(prompt: str = "say hello in five words and stop"):
    started = time.perf_counter()
    snapshot = await read_robot_snapshot(VECTOR_SERIAL, max_age_seconds=60, allow_cooldown_cache=True)
    state = snapshot_to_robot_state(snapshot)
    plan_response = await create_plan(
        PlanRequest(user_text=prompt, robot_state=state),
        model=MODEL,
        ollama_base_url=OLLAMA_BASE_URL,
        execution_mode=EXECUTION_MODE,
    )
    result = {
        "ok": True,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "actions": [action.model_dump() for action in plan_response.actions],
        "safety_notes": plan_response.safety_notes,
        "metrics": plan_response.metrics,
        "robot_state": state.model_dump(exclude_none=True),
    }
    add_event("latency_sample", result)
    return result


async def _live_robot_state(fallback: RobotState) -> RobotState:
    snapshot = await read_robot_snapshot(VECTOR_SERIAL)
    return snapshot_to_robot_state(snapshot, fallback)


def _robot_safe_to_command(snapshot: dict) -> bool:
    if snapshot.get("connected") is not True:
        return False
    unsafe_flags = ("picked_up", "being_held", "cliff_detected", "low_battery", "sleeping")
    return not any(bool(snapshot.get(flag)) for flag in unsafe_flags)


def _safe_control_validation_actions(actions: list[Action], *, allow_drive: bool) -> tuple[list[Action], list[dict]]:
    safe_actions: list[Action] = []
    denied_actions: list[dict] = []
    allowed = {"head", "lift", "turn", "stop", "say", "animation"}
    if allow_drive:
        allowed.add("drive")

    for action in actions:
        action_type = getattr(action, "type", None)
        if action_type == "drive" and allow_drive:
            if abs(action.speed_mmps) <= 35 and action.duration_ms <= 600:
                safe_actions.append(action)
            else:
                denied_actions.append({"action": action.model_dump(), "reason": "drive exceeds validation limit"})
        elif action_type == "turn":
            if abs(action.degrees) <= 35:
                safe_actions.append(action)
            else:
                denied_actions.append({"action": action.model_dump(), "reason": "turn exceeds validation limit"})
        elif action_type in allowed:
            safe_actions.append(action)
        else:
            denied_actions.append({"action": action.model_dump(), "reason": "not allowed during control validation"})

    if safe_actions and safe_actions[-1].type != "stop":
        safe_actions.append(StopAction(type="stop"))
    return safe_actions, denied_actions


def _copy_validation_image(capture: dict, label: str) -> str | None:
    path_value = capture.get("path")
    if not path_value:
        return None
    path = Path(path_value)
    if not path.exists():
        return None
    copy_path = path.with_name(f"{label}.{time.time_ns()}.jpg")
    try:
        shutil.copy2(path, copy_path)
        return str(copy_path)
    except Exception:
        return None


def _observed_control_change(
    before_snapshot: dict,
    after_snapshot: dict,
    before_image: str | None,
    after_image: str | None,
) -> dict[str, Any]:
    head_delta = _numeric_delta(before_snapshot.get("head_angle_deg"), after_snapshot.get("head_angle_deg"))
    lift_delta = _numeric_delta(before_snapshot.get("lift_height_mm"), after_snapshot.get("lift_height_mm"))
    angle_delta = _pose_angle_delta(before_snapshot.get("pose"), after_snapshot.get("pose"))
    frame_delta = _image_mean_abs_delta(before_image, after_image)
    physical_change_detected = any(
        [
            head_delta is not None and abs(head_delta) >= 4.0,
            lift_delta is not None and abs(lift_delta) >= 3.0,
            angle_delta is not None and abs(angle_delta) >= 4.0,
            frame_delta is not None and frame_delta >= 2.0,
        ]
    )
    return {
        "physical_change_detected": physical_change_detected,
        "head_angle_delta_deg": head_delta,
        "lift_height_delta_mm": lift_delta,
        "pose_angle_delta_deg": angle_delta,
        "external_frame_mean_abs_delta": frame_delta,
        "before_image": before_image,
        "after_image": after_image,
    }


def _expected_action_confirmation(
    actions: list[Action],
    before_snapshot: dict,
    after_snapshot: dict,
    observed: dict[str, Any],
) -> dict[str, Any]:
    confirmations: list[dict[str, Any]] = []
    for action in actions:
        if action.type == "stop":
            continue
        if action.type == "head":
            before = _optional_float_local(before_snapshot.get("head_angle_deg"))
            after = _optional_float_local(after_snapshot.get("head_angle_deg"))
            target = float(action.angle_deg)
            confirmed = _moved_toward_target(before, after, target, minimum_delta=4.0, target_tolerance=10.0)
            confirmations.append(
                {
                    "type": "head",
                    "target_angle_deg": target,
                    "before_angle_deg": before,
                    "after_angle_deg": after,
                    "confirmed": confirmed,
                    "method": "head telemetry moved toward target",
                }
            )
        elif action.type == "lift":
            targets = {"low": 32.0, "medium": 62.0, "high": 92.0}
            before = _optional_float_local(before_snapshot.get("lift_height_mm"))
            after = _optional_float_local(after_snapshot.get("lift_height_mm"))
            target = targets[action.height]
            confirmed = _moved_toward_target(before, after, target, minimum_delta=3.0, target_tolerance=8.0)
            confirmations.append(
                {
                    "type": "lift",
                    "target_height": action.height,
                    "target_height_mm": target,
                    "before_height_mm": before,
                    "after_height_mm": after,
                    "confirmed": confirmed,
                    "method": "lift telemetry moved toward target",
                }
            )
        elif action.type == "turn":
            delta = _optional_float_local(observed.get("pose_angle_delta_deg"))
            expected_sign = 1 if action.degrees > 0 else -1
            confirmed = delta is not None and abs(delta) >= 4.0 and (1 if delta > 0 else -1) == expected_sign
            confirmations.append(
                {
                    "type": "turn",
                    "target_degrees": action.degrees,
                    "pose_angle_delta_deg": delta,
                    "confirmed": confirmed,
                    "method": "pose heading changed in expected direction",
                }
            )
        elif action.type == "drive":
            distance = _pose_distance(before_snapshot.get("pose"), after_snapshot.get("pose"))
            confirmed = distance is not None and distance >= 8.0
            confirmations.append(
                {
                    "type": "drive",
                    "target_speed_mmps": action.speed_mmps,
                    "target_duration_ms": action.duration_ms,
                    "pose_distance_mm": distance,
                    "confirmed": confirmed,
                    "method": "pose position changed enough to distinguish from sensor noise",
                }
            )
        elif action.type in {"animation", "behavior"}:
            frame_delta = _optional_float_local(observed.get("external_frame_mean_abs_delta"))
            confirmed = frame_delta is not None and frame_delta >= 3.0
            confirmations.append(
                {
                    "type": action.type,
                    "name": getattr(action, "name", None),
                    "external_frame_mean_abs_delta": frame_delta,
                    "confirmed": confirmed,
                    "method": "external camera detected visible change",
                }
            )
        elif action.type == "say":
            confirmations.append(
                {
                    "type": "say",
                    "confirmed": True,
                    "method": "speech action was accepted by executor; audio validation is not yet implemented",
                }
            )

    actionable = [item for item in confirmations if item["type"] != "say"]
    if not actionable:
        actionable = confirmations
    return {
        "confirmed": bool(actionable and all(item.get("confirmed") for item in actionable)),
        "checks": confirmations,
    }


def _moved_toward_target(
    before: float | None,
    after: float | None,
    target: float,
    *,
    minimum_delta: float,
    target_tolerance: float,
) -> bool:
    if before is None or after is None:
        return False
    if abs(after - target) <= target_tolerance:
        return True
    before_error = abs(before - target)
    after_error = abs(after - target)
    return after_error < before_error and abs(after - before) >= minimum_delta


def _pose_distance(before: Any, after: Any) -> float | None:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    try:
        dx = float(after.get("x")) - float(before.get("x"))
        dy = float(after.get("y")) - float(before.get("y"))
        return round((dx * dx + dy * dy) ** 0.5, 2)
    except (TypeError, ValueError):
        return None


def _optional_float_local(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _numeric_delta(before: Any, after: Any) -> float | None:
    try:
        return round(float(after) - float(before), 2)
    except (TypeError, ValueError):
        return None


def _pose_angle_delta(before: Any, after: Any) -> float | None:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    return _numeric_delta(before.get("angle_deg"), after.get("angle_deg"))


def _image_mean_abs_delta(before_path: str | None, after_path: str | None) -> float | None:
    if not before_path or not after_path:
        return None
    try:
        from PIL import Image, ImageChops, ImageStat

        with Image.open(before_path) as before_image, Image.open(after_path) as after_image:
            before_gray = before_image.convert("L").resize((160, 90))
            after_gray = after_image.convert("L").resize((160, 90))
            diff = ImageChops.difference(before_gray, after_gray)
            stat = ImageStat.Stat(diff)
            return round(float(stat.mean[0]), 2)
    except Exception:
        return None


@app.get("/robot/state")
async def robot_state(live: bool = False):
    snapshot = await read_robot_snapshot(
        VECTOR_SERIAL,
        max_age_seconds=0 if live else 60,
        allow_cooldown_cache=not live,
    )
    latest_vision = get_latest_vision()
    if latest_vision:
        snapshot["latest_vision"] = latest_vision
    latest_external_view = get_latest_external_view()
    if latest_external_view:
        snapshot["latest_external_view"] = latest_external_view
    return snapshot


@app.post("/robot/look")
async def robot_look():
    result = await capture_and_describe_view(
        VECTOR_SERIAL,
        ollama_base_url=OLLAMA_BASE_URL,
        model=VISION_MODEL,
        output_dir=CAPTURE_DIR,
    )
    add_event("vision", result)
    return result


@app.get("/robot/latest.jpg")
async def robot_latest_image():
    path = CAPTURE_DIR / "latest.jpg"
    if not path.exists():
        raise HTTPException(status_code=404, detail="no camera image has been captured yet")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/external-camera/status")
async def external_camera_status_route():
    return external_camera_status(CAPTURE_DIR)


@app.post("/external-camera/capture")
async def external_camera_capture(describe: bool = True):
    result = await capture_external_view(
        output_dir=CAPTURE_DIR,
        ollama_base_url=OLLAMA_BASE_URL,
        model=EXTERNAL_CAMERA_VISION_MODEL,
        describe=describe,
    )
    if describe:
        _schedule_llm_warmup("external_camera_capture")
    add_event("external_camera", result)
    return result


@app.get("/external-camera/latest.jpg")
async def external_camera_latest_image():
    path = CAPTURE_DIR / EXTERNAL_CAMERA_LATEST_FILENAME
    if not path.exists():
        raise HTTPException(status_code=404, detail="no external camera image has been captured yet")
    return FileResponse(path, media_type="image/jpeg")


@app.post("/validation/pip-area")
async def validate_pip_area():
    result = await capture_external_view(
        output_dir=CAPTURE_DIR,
        ollama_base_url=OLLAMA_BASE_URL,
        model=EXTERNAL_CAMERA_VISION_MODEL,
        describe=True,
    )
    _schedule_llm_warmup("pip_area_validation")
    robot_snapshot = await read_robot_snapshot(
        VECTOR_SERIAL,
        max_age_seconds=60,
        allow_cooldown_cache=True,
    )
    validation = {
        "external_camera": result.get("validation"),
        "robot_connected": robot_snapshot.get("connected") is True,
        "robot_safe_to_command": _robot_safe_to_command(robot_snapshot),
        "robot_state": robot_snapshot,
    }
    validation["ok"] = bool(
        validation["external_camera"]
        and validation["external_camera"].get("ok")
        and validation["robot_connected"]
        and validation["robot_safe_to_command"]
    )
    add_event("pip_area_validation", {"capture": result, "validation": validation})
    return {"capture": result, "validation": validation}


@app.post("/validation/gemma-control")
async def validate_gemma_control(
    prompt: str = "Validation only: move your head up to 35 degrees, then stop. Do not drive.",
    dry_run: bool = False,
    allow_drive: bool = False,
):
    before_capture = await capture_external_view(
        output_dir=CAPTURE_DIR,
        ollama_base_url=OLLAMA_BASE_URL,
        model=EXTERNAL_CAMERA_VISION_MODEL,
        describe=True,
    )
    _schedule_llm_warmup("gemma_control_before_capture")
    before_image = _copy_validation_image(before_capture, "gemma-control-before")
    before_snapshot = await read_robot_snapshot(VECTOR_SERIAL, max_age_seconds=0, allow_cooldown_cache=False)
    if not dry_run and not _robot_safe_to_command(before_snapshot):
        validation = {
            "ok": False,
            "reason": "robot is not in a safe state for physical validation",
            "robot_state": before_snapshot,
            "external_camera": before_capture.get("validation"),
        }
        add_event("gemma_control_validation", validation)
        return {"validation": validation}

    robot_state = snapshot_to_robot_state(before_snapshot)
    plan_response = await create_plan(
        PlanRequest(user_text=prompt, robot_state=robot_state),
        model=MODEL,
        ollama_base_url=OLLAMA_BASE_URL,
        execution_mode=EXECUTION_MODE,
    )
    safe_actions, denied_actions = _safe_control_validation_actions(plan_response.actions, allow_drive=allow_drive)
    if not safe_actions:
        validation = {
            "ok": False,
            "reason": "Gemma did not propose a safe visible action for this validation.",
            "planned_actions": [action.model_dump() for action in plan_response.actions],
            "denied_actions": denied_actions,
        }
        add_event("gemma_control_validation", validation)
        return {"plan": plan_response, "validation": validation}

    executor = get_executor(EXECUTION_MODE, serial=VECTOR_SERIAL)
    execute_response = await executor.execute(
        ExecuteRequest(actions=safe_actions, robot_state=robot_state, dry_run=dry_run)
    )
    await asyncio.sleep(1.25)

    after_snapshot = await read_robot_snapshot(VECTOR_SERIAL, max_age_seconds=0, allow_cooldown_cache=False)
    after_capture = await capture_external_view(
        output_dir=CAPTURE_DIR,
        ollama_base_url=OLLAMA_BASE_URL,
        model=EXTERNAL_CAMERA_VISION_MODEL,
        describe=True,
    )
    _schedule_llm_warmup("gemma_control_after_capture")
    after_image = _copy_validation_image(after_capture, "gemma-control-after")
    observed = _observed_control_change(before_snapshot, after_snapshot, before_image, after_image)
    expected = _expected_action_confirmation(safe_actions, before_snapshot, after_snapshot, observed)
    before_camera = before_capture.get("validation") or {}
    after_camera = after_capture.get("validation") or {}
    action_errors = [
        item for item in execute_response.executed
        if isinstance(item, dict) and item.get("error")
    ]
    validation = {
        "ok": bool(
            before_camera.get("pip_visible")
            and before_camera.get("image_ok")
            and after_camera.get("image_ok")
            and execute_response.ok
            and (dry_run or observed["physical_change_detected"])
            and (dry_run or expected["confirmed"])
        ),
        "dry_run": dry_run,
        "prompt": prompt,
        "safe_actions": [action.model_dump() for action in safe_actions],
        "denied_actions": denied_actions,
        "action_errors": action_errors,
        "observed": observed,
        "expected": expected,
        "external_camera_before": before_camera,
        "external_camera_after": after_camera,
    }
    add_event(
        "gemma_control_validation",
        {
            "plan": {
                "actions": [action.model_dump() for action in plan_response.actions],
                "raw": plan_response.raw,
            },
            "execute": execute_response.model_dump(),
            "validation": validation,
        },
    )
    return {
        "plan": plan_response,
        "execute": execute_response,
        "before_capture": before_capture,
        "after_capture": after_capture,
        "before_state": before_snapshot,
        "after_state": after_snapshot,
        "validation": validation,
    }


@app.post("/validation/control-suite")
async def validate_control_suite(
    dry_run: bool = True,
    include_turn: bool = False,
    include_drive: bool = False,
):
    prompts = [
        "Validation only: move your head up to 35 degrees, then stop. Do not drive.",
        "Validation only: move your head down to -10 degrees, then stop. Do not drive.",
        "Validation only: raise your lift high, then stop. Do not drive.",
        "Validation only: lower your lift low, then stop. Do not drive.",
    ]
    if include_turn:
        prompts.extend(
            [
                "Validation only: turn left 20 degrees, then stop. Do not drive forward.",
                "Validation only: turn right 20 degrees, then stop. Do not drive forward.",
            ]
        )
    if include_drive:
        prompts.append("Validation only: drive forward very slowly for half a second, then stop.")

    results = []
    for prompt in prompts:
        result = await validate_gemma_control(
            prompt=prompt,
            dry_run=dry_run,
            allow_drive=include_drive,
        )
        results.append(result)
        await asyncio.sleep(1.0)

    validations = [item.get("validation", {}) for item in results if isinstance(item, dict)]
    ok = bool(validations and all(item.get("ok") for item in validations))
    response = {"ok": ok, "dry_run": dry_run, "results": results}
    add_event("control_suite_validation", {"ok": ok, "dry_run": dry_run, "count": len(results)})
    return response


@app.get("/map/status")
async def map_status():
    return load_environment_map(CAPTURE_DIR)


@app.post("/map/observe")
async def map_observe(note: str | None = None, include_robot_camera: bool = False):
    robot_snapshot = await read_robot_snapshot(VECTOR_SERIAL, max_age_seconds=0, allow_cooldown_cache=False)
    external_view = await capture_external_view(
        output_dir=CAPTURE_DIR,
        ollama_base_url=OLLAMA_BASE_URL,
        model=MAP_VISION_MODEL,
        describe=True,
    )
    _schedule_llm_warmup("map_observe")
    robot_view = None
    if include_robot_camera:
        robot_view = await capture_and_describe_view(
            VECTOR_SERIAL,
            ollama_base_url=OLLAMA_BASE_URL,
            model=VISION_MODEL,
            output_dir=CAPTURE_DIR,
        )
    data = record_environment_observation(
        CAPTURE_DIR,
        robot_state=robot_snapshot,
        external_view=external_view,
        robot_view=robot_view,
        note=note,
    )
    add_event("map_observation", {"summary": data.get("summary"), "note": note})
    return data


@app.get("/vision/status", response_model=VisionStatus)
async def vision_status():
    return vision_loop.status()


@app.post("/vision/start", response_model=VisionStatus)
async def vision_start(req: VisionConfig):
    status = await vision_loop.start(req)
    add_event("vision_loop", {"status": "started", **status.model_dump()})
    return status


@app.post("/vision/stop", response_model=VisionStatus)
async def vision_stop():
    status = await vision_loop.stop()
    add_event("vision_loop", {"status": "stopped", **status.model_dump()})
    return status


@app.post("/vision/tick", response_model=VisionStatus)
async def vision_tick(req: VisionConfig):
    status = await vision_loop.tick(req)
    add_event("vision_loop", {"status": "ticked", **status.model_dump()})
    return status


@app.get("/audio/status")
async def audio_status():
    return audio_capture.status()


@app.post("/audio/start")
async def audio_start():
    status = await audio_capture.start()
    add_event("audio_capture", {"status": "started", **status})
    return status


@app.post("/audio/stop")
async def audio_stop():
    status = await audio_capture.stop()
    add_event("audio_capture", {"status": "stopped", **status})
    return status


@app.websocket("/audio")
async def audio_websocket(websocket: WebSocket):
    await websocket.accept()
    include_signal = websocket.query_params.get("include_signal") in {"1", "true", "yes"}
    try:
        if not audio_capture.enabled:
            await audio_capture.start()
        await websocket.send_json({"type": "status", "audio": audio_capture.status()})
        async for frame in audio_capture.frames(include_existing_latest=True):
            await websocket.send_json({"type": "audio_frame", "frame": frame.to_dict(include_signal=include_signal)})
    except WebSocketDisconnect:
        return


@app.get("/listener/status", response_model=ListenerStatus)
async def listener_status():
    return listener.status()


@app.post("/listener/start", response_model=ListenerStatus)
async def listener_start(req: ListenerConfig):
    if conversation_session.config.enabled:
        req = req.model_copy(update={"auto_route": False})
    status = await listener.start(req)
    add_event("listener", {"status": "started", **status.model_dump()})
    return status


@app.post("/listener/stop", response_model=ListenerStatus)
async def listener_stop():
    status = await listener.stop()
    add_event("listener", {"status": "stopped", **status.model_dump()})
    return status


@app.post("/listener/mute", response_model=ListenerStatus)
async def listener_mute(seconds: float = 5.0):
    listener.mute_for(seconds)
    status = listener.status()
    add_event("listener", {"status": "muted", "seconds": seconds, **status.model_dump()})
    return status


@app.get("/listener/pending")
async def listener_pending():
    transcript = await listener.pop_pending()
    return {"transcript": transcript}


@app.post("/plan", response_model=PlanResponse)
async def plan(req: PlanRequest):
    try:
        robot_state = await _live_robot_state(req.robot_state)
        response = await create_plan(
            PlanRequest(user_text=req.user_text, robot_state=robot_state),
            model=MODEL,
            ollama_base_url=OLLAMA_BASE_URL,
            execution_mode=EXECUTION_MODE,
        )
        add_event(
            "plan",
            {
                "user_text": req.user_text,
                "robot_state": robot_state.model_dump(exclude_none=True),
                "actions": [action.model_dump() for action in response.actions],
                "denied_actions": response.denied_actions,
                "safety_notes": response.safety_notes,
                "raw": response.raw,
                "metrics": response.metrics,
                "model": response.model,
                "execution_mode": response.execution_mode,
            },
        )
        return response
    except Exception as exc:
        add_event("error", {"where": "plan", "error": str(exc)})
        raise HTTPException(status_code=502, detail=f"planner request failed: {exc}")


@app.post("/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest):
    try:
        robot_state = await _live_robot_state(req.robot_state)
        executor = get_executor(EXECUTION_MODE, serial=VECTOR_SERIAL)
        response = await executor.execute(ExecuteRequest(actions=req.actions, robot_state=robot_state, dry_run=req.dry_run))
        add_event(
            "execute",
            {
                "robot_state": robot_state.model_dump(exclude_none=True),
                "dry_run": req.dry_run,
                "executed": response.executed,
                "denied_actions": response.denied_actions,
                "safety_notes": response.safety_notes,
                "mode": response.mode,
            },
        )
        return response
    except Exception as exc:
        add_event("error", {"where": "execute", "error": str(exc)})
        raise HTTPException(status_code=500, detail=f"executor failed: {exc}")


@app.post("/plan_execute", response_model=ExecuteResponse)
async def plan_execute(req: PlanExecuteRequest):
    plan_response = await plan(PlanRequest(user_text=req.user_text, robot_state=req.robot_state))
    exec_req = ExecuteRequest(actions=plan_response.actions, robot_state=req.robot_state, dry_run=req.dry_run)
    return await execute(exec_req)


@app.post("/chat")
async def chat(req: ChatRequest):
    return await _run_chat(req, event_kind="chat")


async def _run_chat(req: ChatRequest, *, event_kind: str, source: dict | None = None):
    robot_state = await _live_robot_state(req.robot_state)
    plan_response = await create_conversation_plan(
        PlanRequest(user_text=req.user_text, robot_state=robot_state),
        model=MODEL,
        ollama_base_url=OLLAMA_BASE_URL,
        execution_mode=EXECUTION_MODE,
    )
    say_text = " ".join(action.text for action in plan_response.actions if action.type == "say")
    if say_text:
        remember_turn(req.user_text, say_text)
        # A real conversation just happened — signal the goal engine so CELEBRATING can fire
        goal_engine.notify_positive_interaction()
    execute_response = None
    if req.execute:
        exec_req = ExecuteRequest(actions=plan_response.actions, robot_state=robot_state, dry_run=req.dry_run)
        execute_response = await execute(exec_req)
    add_event(
        event_kind,
        {
            "user_text": req.user_text,
            "assistant_text": say_text,
            "robot_state": robot_state.model_dump(exclude_none=True),
            "actions": [action.model_dump() for action in plan_response.actions],
            "executed": execute_response.model_dump() if execute_response else None,
            "raw": plan_response.raw,
            "metrics": plan_response.metrics,
            "source": source,
        },
    )
    return {"plan": plan_response, "execute": execute_response}


async def _route_listener_transcript(text: str, payload: dict, config: ListenerConfig):
    add_event(
        "listener_transcript",
        {
            "text": text,
            "execute": config.execute,
            "dry_run": config.dry_run,
            "auto_route": config.auto_route,
            "utterance": payload,
        },
    )
    if conversation_session.is_active():
        return await conversation_session.on_transcript(text, payload)
    if config.auto_route:
        return await _run_chat(
            ChatRequest(user_text=text, execute=config.execute, dry_run=config.dry_run),
            event_kind="listener_chat",
            source={"listener": payload},
        )
    return {"ignored": True, "reason": "listener auto_route disabled and conversation idle"}


listener.route_callback = _route_listener_transcript


@app.get("/conversation/status", response_model=ConversationSessionStatus)
async def conversation_status():
    return conversation_session.status()


@app.post("/conversation/config", response_model=ConversationSessionStatus)
async def conversation_config(req: ConversationSessionConfig):
    status = conversation_session.update_config(req)
    add_event("conversation_config", status.model_dump())
    return status


@app.post("/conversation/reset", response_model=ConversationSessionStatus)
async def conversation_reset(reason: str = "manual reset"):
    status = await conversation_session.reset(reason)
    add_event("conversation_reset", {"reason": reason, "status": status.model_dump()})
    return status


async def _autonomy_listen_callback() -> None:
    """
    Called by the autonomy loop after Pip speaks (when listen_after_speech=True).
    Opens an 8-second window: polls the Whisper listener for a transcript,
    then routes it through Gemma exactly like a normal chat interaction.

    Design notes (2026-05-14):
    - We drain stale pending transcripts first so we don't route something Rob
      said before Pip finished speaking.
    - We temporarily override muted_until so Vector's own speech playback mute
      doesn't block the reply window.
    - If the listener isn't enabled, we skip silently — caller doesn't break.
    - After routing a reply, we mute for 3s to avoid echo from Pip's response.
    """
    if not listener.config.enabled:
        return

    # Drain any transcripts that arrived before the listen window opened
    # (leftovers from before Pip spoke, or echos of Pip's own TTS).
    drained = 0
    while True:
        stale = await listener.pop_pending()
        if stale is None:
            break
        drained += 1
    if drained:
        add_event("autonomy_listen_drain", {"drained": drained})

    # Override any mute so the reply window is actually open.
    listener.muted_until = None

    add_event("autonomy_listen_open", {"window_s": 8})

    deadline = asyncio.get_event_loop().time() + 8.0
    while asyncio.get_event_loop().time() < deadline:
        transcript = await listener.pop_pending()
        if transcript:
            text = transcript.get("text", "").strip()
            if text:
                add_event("autonomy_listen_reply", {"text": text, "transcript": transcript})
                try:
                    await _run_chat(
                        ChatRequest(user_text=text, execute=True, dry_run=False),
                        event_kind="autonomy_listen_chat",
                        source={"autonomy_listen": transcript},
                    )
                except Exception as exc:
                    add_event("autonomy_listen_error", {"error": str(exc)})
                finally:
                    listener.mute_for(3.0)   # brief mute so Pip's reply isn't re-transcribed
            return
        await asyncio.sleep(0.25)

    add_event("autonomy_listen_timeout", {"window_s": 8, "note": "no reply in listen window"})


# Wire the same listen callback into autonomy, sentinel, and voice_bridge.
# All three share the identical poll-and-route logic: after Pip speaks, open
# an 8s Whisper window, wait for Rob's reply, route through Gemma.
# listen_after_speech must be enabled in each subsystem's config to activate.
autonomy.listen_callback = _autonomy_listen_callback
sentinel.listen_callback = _autonomy_listen_callback
voice_bridge.listen_callback = _autonomy_listen_callback
voice_bridge.wake_callback = lambda text, payload, dry_run, execute: conversation_session.on_wake_word(
    text,
    payload,
    execute=execute,
    dry_run=dry_run,
)


def _start_background_task(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return task


@app.post("/wirepod/transcript")
async def wirepod_transcript(req: WirePodTranscriptRequest):
    text = " ".join(req.text.strip().strip("\"").split())
    payload = req.model_dump()
    payload["text"] = text
    add_event("wirepod_transcript", payload)
    if conversation_session.config.enabled:
        _start_background_task(
            conversation_session.on_wake_word(
                text,
                payload,
                execute=req.execute,
                dry_run=req.dry_run,
            )
        )
    else:
        _start_background_task(_route_wirepod_transcript(text, payload, req))
    return {"ok": True, "queued": True}


async def _route_wirepod_transcript(text: str, payload: dict, req: WirePodTranscriptRequest):
    try:
        is_command = _is_explicit_robot_command(text)
        robot_state = await _wirepod_robot_state(force_live=is_command)
        plan_fn = create_plan if is_command else create_conversation_plan
        plan_response = await plan_fn(
            PlanRequest(user_text=text, robot_state=robot_state),
            model=MODEL,
            ollama_base_url=OLLAMA_BASE_URL,
            execution_mode=EXECUTION_MODE,
        )
        say_text = " ".join(action.text for action in plan_response.actions if action.type == "say")
        if say_text:
            remember_turn(text, say_text)
            goal_engine.notify_positive_interaction()

        if is_command:
            plan_response.actions = _repair_command_actions(text, plan_response.actions)
        else:
            plan_response.actions = _conversation_only_actions(plan_response.actions)
        execute_response = None
        execution_error = None
        add_event(
            "wirepod_chat",
            {
                "user_text": text,
                "assistant_text": say_text,
                "is_command": is_command,
                "robot_state": robot_state.model_dump(exclude_none=True),
                "actions": [action.model_dump() for action in plan_response.actions],
                "executed": execute_response.model_dump() if execute_response else None,
                "raw": plan_response.raw,
                "metrics": plan_response.metrics,
                "source": {"wirepod": payload},
            },
        )

        if req.execute:
            executor = get_executor(EXECUTION_MODE, serial=VECTOR_SERIAL)
            try:
                execute_response = await executor.execute(
                    ExecuteRequest(actions=plan_response.actions, robot_state=robot_state, dry_run=req.dry_run)
                )
            except Exception as exc:
                execution_error = str(exc) or repr(exc)

        if execute_response or execution_error:
            add_event(
                "wirepod_execute",
                {
                    "user_text": text,
                    "executed": execute_response.model_dump() if execute_response else None,
                    "execution_error": execution_error,
                },
            )
        result = {"plan": plan_response, "execute": execute_response}
        add_event(
            "wirepod_execute_complete",
            {
                "text": text,
                "serial": req.serial,
                "execute": req.execute,
                "dry_run": req.dry_run,
                "result": result,
            },
        )
    except Exception as exc:
        add_event("error", {"where": "wirepod_transcript", "error": str(exc) or repr(exc), "payload": payload})


async def _wirepod_robot_state(*, force_live: bool = False) -> RobotState:
    if force_live:
        try:
            return snapshot_to_robot_state(
                await read_robot_snapshot(VECTOR_SERIAL, max_age_seconds=2, allow_cooldown_cache=False)
            )
        except Exception:
            pass
    cached = get_cached_robot_snapshot(max_age_seconds=30)
    if cached:
        return snapshot_to_robot_state(cached)
    return RobotState()


# ── WirePod transcript helpers ─────────────────────────────────────────────────

_EXPLICIT_COMMAND_KEYWORDS = {
    "drive", "move", "go", "forward", "backward", "reverse", "turn", "spin",
    "rotate", "left", "right", "stop", "dock", "charge", "home", "look",
    "find", "cube", "roll", "lift", "raise", "lower", "head", "tilt",
    "say", "speak", "tell", "announce", "play", "dance", "celebrate",
    "photo", "picture", "camera", "see", "describe",
}

def _is_explicit_robot_command(text: str) -> bool:
    """
    Return True if the user's text looks like a direct physical command
    (drive, look around, dock) rather than a conversational message.
    Commands get the full action planner; conversation gets create_conversation_plan.
    """
    words = set(text.lower().split())
    return bool(words & _EXPLICIT_COMMAND_KEYWORDS)


def _repair_command_actions(text: str, actions: list) -> list:
    """
    For explicit commands, ensure the action list is not empty and has a stop.
    Strips listen actions (not appropriate for commands) and ensures a stop is present.
    """
    repaired = [a for a in actions if a.type != "listen"]
    if not repaired:
        repaired.append(StopAction(type="stop"))
    elif repaired[-1].type != "stop":
        repaired.append(StopAction(type="stop"))
    return repaired


def _conversation_only_actions(actions: list) -> list:
    """
    For conversational WirePod transcripts, strip motion-heavy actions.
    Keep say, animation, head, lift, listen, stop.
    Drive/turn/behavior are omitted — WirePod conversations shouldn't move the robot.
    """
    allowed = {"say", "animation", "head", "lift", "listen", "stop"}
    filtered = [a for a in actions if a.type in allowed]
    if not filtered:
        filtered.append(StopAction(type="stop"))
    elif filtered[-1].type != "stop":
        filtered.append(StopAction(type="stop"))
    return filtered


# ── Autonomy routes ────────────────────────────────────────────────────────────

@app.get("/autonomy/status", response_model=AutonomyStatus)
async def autonomy_status():
    return autonomy.status()


@app.post("/autonomy/tick", response_model=AutonomyStatus)
async def autonomy_tick(req: AutonomyConfig):
    return await autonomy.tick(req)


@app.post("/autonomy/start", response_model=AutonomyStatus)
async def autonomy_start(req: AutonomyConfig):
    return await autonomy.start(req)


@app.post("/autonomy/stop", response_model=AutonomyStatus)
async def autonomy_stop():
    return await autonomy.stop()


# ── Voice bridge routes ────────────────────────────────────────────────────────

@app.get("/voice/status", response_model=VoiceBridgeStatus)
async def voice_status():
    return voice_bridge.status()


@app.post("/voice/start", response_model=VoiceBridgeStatus)
async def voice_start(req: VoiceBridgeConfig):
    return await voice_bridge.start(req)


@app.post("/voice/stop", response_model=VoiceBridgeStatus)
async def voice_stop():
    return await voice_bridge.stop()


# ── Emotion routes ─────────────────────────────────────────────────────────────

@app.get("/emotion/state")
async def emotion_state():
    """Return Pip's current emotional state and metadata."""
    return emotion_engine.to_dict()


@app.post("/emotion/set")
async def emotion_set(state: str, reason: str = "manual override"):
    """
    Force Pip's emotion to a specific state.
    Valid states: CURIOUS, CONTENT, ALERT, PLAYFUL, CAUTIOUS, TIRED, EXCITED
    """
    try:
        emotion_engine.force_state(state.upper(), reason=reason)
        add_event("emotion_override", {"state": state.upper(), "reason": reason})
        return emotion_engine.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ── Goal routes ────────────────────────────────────────────────────────────────

@app.get("/goals/state")
async def goals_state():
    """Return Pip's current active goal and tick progress."""
    return goal_engine.to_dict()


@app.post("/goals/set")
async def goals_set(goal: str, reason: str = "manual override"):
    """
    Force Pip's active goal.
    Valid goals: EXPLORING, WATCHING, INVESTIGATING, SOCIALIZING,
                 CELEBRATING, SEEKING_CHARGER, RESTING
    """
    try:
        goal_engine.force_goal(goal.upper(), reason=reason)
        add_event("goal_override", {"goal": goal.upper(), "reason": reason})
        return goal_engine.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ── Sentinel routes ────────────────────────────────────────────────────────────

@app.get("/sentinel/status", response_model=SentinelStatus)
async def sentinel_status():
    """Return sentinel loop status, poll count, last event, and per-event cooldowns."""
    return sentinel.status()


@app.post("/sentinel/start", response_model=SentinelStatus)
async def sentinel_start(req: SentinelConfig):
    """Start the sentinel event-detection loop (polls every 2s by default)."""
    status = await sentinel.start(req)
    add_event("sentinel", {"status": "started", **status.model_dump()})
    return status


@app.post("/sentinel/stop", response_model=SentinelStatus)
async def sentinel_stop():
    """Stop the sentinel loop."""
    status = await sentinel.stop()
    add_event("sentinel", {"status": "stopped", **status.model_dump()})
    return status
