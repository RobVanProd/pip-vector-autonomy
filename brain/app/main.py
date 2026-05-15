from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse

from .audio_capture import AudioCapture
from .autonomy import AutonomyLoop
from .dashboard import DASHBOARD_HTML
from .emotion import EmotionEngine
from .events import add_event, list_events
from .executors import get_executor
from .goals import GoalEngine
from .listener import Listener
from .memory import add_fact, load_memory, remember_turn
from .openai_proxy import chat_completion
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
    BehaviorAction,
    ChatRequest,
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
background_tasks: set[asyncio.Task] = set()


@app.get("/health")
async def health():
    return {
        "ok": True,
        "model": MODEL,
        "ollama": OLLAMA_BASE_URL,
        "execution_mode": EXECUTION_MODE,
        "vector_serial": VECTOR_SERIAL,
        "vision_model": VISION_MODEL,
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


async def _live_robot_state(fallback: RobotState) -> RobotState:
    snapshot = await read_robot_snapshot(VECTOR_SERIAL)
    return snapshot_to_robot_state(snapshot, fallback)


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
    return await _run_chat(
        ChatRequest(user_text=text, execute=config.execute, dry_run=config.dry_run),
        event_kind="listener_chat",
        source={"listener": payload},
    )


listener.route_callback = _route_listener_transcript


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


def _start_background_task(coro) -> None:
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


@app.post("/wirepod/transcript")
async def wirepod_transcript(req: WirePodTranscriptRequest):
    text = " ".join(req.text.strip().strip("\"").split())
    payload = req.model_dump()
    payload["text"] = text
    add_event("wirepod_transcript", payload)
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
