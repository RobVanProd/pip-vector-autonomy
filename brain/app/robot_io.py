from __future__ import annotations

import asyncio
import base64
import io
import math
import time
from pathlib import Path
from typing import Any

from PIL import Image

from .ollama_runtime import ollama_generate
from .robot_lock import SDK_LOCK
from .schemas import RobotState

STATE_CACHE_SECONDS = 8
COOLDOWN_STATE_CACHE_SECONDS = 300
_STATE_CACHE: dict[str, Any] = {"ts": 0.0, "snapshot": None}
_LATEST_VISION: dict[str, Any] = {}
_VISION_CAPTURE_LOCK = asyncio.Lock()
_BATTERY_VOLTAGE_CURVE = [
    (3.30, 0),
    (3.50, 10),
    (3.62, 25),
    (3.70, 40),
    (3.78, 55),
    (3.86, 70),
    (3.95, 85),
    (4.10, 95),
    (4.20, 100),
]


async def read_robot_snapshot(
    serial: str | None,
    *,
    max_age_seconds: int = STATE_CACHE_SECONDS,
    allow_cooldown_cache: bool = True,
) -> dict[str, Any]:
    if not serial:
        return {
            "connected": False,
            "notes": "VECTOR_SERIAL is not configured, so live robot state is unavailable.",
        }

    now = time.time()
    cached = _STATE_CACHE.get("snapshot")
    if cached:
        effective_max_age = max_age_seconds
        if allow_cooldown_cache and _prefers_cooldown_cache(cached):
            effective_max_age = max(effective_max_age, COOLDOWN_STATE_CACHE_SECONDS)
        if now - float(_STATE_CACHE.get("ts", 0.0)) < effective_max_age:
            snapshot = dict(cached)
            snapshot["snapshot_age_seconds"] = round(now - float(_STATE_CACHE.get("ts", 0.0)), 1)
            snapshot["snapshot_source"] = "cache"
            return snapshot

    async with SDK_LOCK:
        snapshot = await asyncio.to_thread(_read_robot_snapshot_sync, serial)
    _STATE_CACHE["ts"] = time.time()
    _STATE_CACHE["snapshot"] = snapshot
    return dict(snapshot)


def _prefers_cooldown_cache(snapshot: dict[str, Any]) -> bool:
    return bool(
        snapshot.get("connected") is not False
        and (
            snapshot.get("on_charger")
            or snapshot.get("charging")
            or snapshot.get("calm_power_mode")
            or snapshot.get("sleeping")
        )
    )


def snapshot_to_robot_state(snapshot: dict[str, Any], fallback: RobotState | None = None) -> RobotState:
    base = fallback.model_dump(exclude_none=True) if fallback else {}
    for key in RobotState.model_fields:
        if key in snapshot and snapshot[key] is not None:
            base[key] = snapshot[key]

    latest_vision = get_latest_vision()
    if latest_vision.get("description"):
        base["vision_description"] = latest_vision["description"]

    return RobotState(**base)


def get_cached_robot_snapshot(*, max_age_seconds: int = 120) -> dict[str, Any] | None:
    cached = _STATE_CACHE.get("snapshot")
    if not cached:
        return None
    if time.time() - float(_STATE_CACHE.get("ts", 0.0)) > max_age_seconds:
        return None
    return dict(cached)


async def capture_and_describe_view(
    serial: str | None,
    *,
    ollama_base_url: str,
    model: str,
    output_dir: Path,
) -> dict[str, Any]:
    if not serial:
        return {"ok": False, "error": "VECTOR_SERIAL is not configured."}

    async with _VISION_CAPTURE_LOCK:
        async with SDK_LOCK:
            capture = await asyncio.to_thread(_capture_view_sync, serial, output_dir)
    if not capture.get("ok"):
        capture.setdefault("ts", time.time())
        return capture

    description_result = await _describe_with_fallbacks(
        image_b64=capture["image_b64"],
        ollama_base_url=ollama_base_url,
        models=_vision_models(model),
    )
    result = {
        **capture,
        **description_result,
        "image_b64": None,
        "ts": time.time(),
    }
    _remember_vision(result)
    return result


async def describe_image_b64(
    image_b64: str,
    *,
    ollama_base_url: str,
    model: str,
    prompt: str | None = None,
) -> dict[str, Any]:
    return await _describe_with_fallbacks(
        image_b64=image_b64,
        ollama_base_url=ollama_base_url,
        models=_vision_models(model),
        prompt=prompt,
    )


def average_image_brightness(image: Any) -> float | None:
    return _average_brightness(image)


def get_latest_vision() -> dict[str, Any]:
    return dict(_LATEST_VISION)


def _remember_vision(result: dict[str, Any]) -> None:
    _LATEST_VISION.clear()
    _LATEST_VISION.update({k: v for k, v in result.items() if k != "image_b64"})


def _read_robot_snapshot_sync(serial: str) -> dict[str, Any]:
    try:
        import anki_vector

        with anki_vector.Robot(
            serial=serial,
            behavior_control_level=None,
            default_logging=False,
        ) as robot:
            status = robot.status
            battery = robot.get_battery_state()
            on_charger = bool(getattr(status, "is_on_charger", False)) or bool(
                getattr(battery, "is_on_charger_platform", False)
            )
            charging = bool(getattr(status, "is_charging", False)) or bool(getattr(battery, "is_charging", False))
            calm_power_mode = bool(getattr(status, "is_in_calm_power_mode", False))
            battery_level = getattr(battery, "battery_level", None)
            battery_level_value = getattr(battery_level, "value", battery_level)
            battery_volts = getattr(battery, "battery_volts", None)
            battery_percent = _estimate_battery_percent(battery_volts)
            low_battery = _is_low_battery(battery_level_value, battery_volts)
            pose = robot.pose
            return {
                "connected": True,
                "serial": serial,
                "on_charger": on_charger,
                "charging": charging,
                "calm_power_mode": calm_power_mode,
                "sleeping": calm_power_mode or charging,
                "picked_up": bool(getattr(status, "is_picked_up", False)),
                "being_held": bool(getattr(status, "is_being_held", False)),
                "cliff_detected": bool(getattr(status, "is_cliff_detected", False)),
                "face_detected": _has_visible_faces(robot),
                "cube_detected": _has_connected_cube(robot),
                "low_battery": low_battery,
                "battery_volts": float(battery_volts) if battery_volts is not None else None,
                "battery_level": battery_level_value,
                "battery_percent": battery_percent,
                "battery_percent_source": "voltage_estimate_charging" if charging else "voltage_estimate",
                "head_angle_deg": _radians_to_degrees(getattr(robot, "head_angle_rad", None)),
                "lift_height_mm": _optional_float(getattr(robot, "lift_height_mm", None)),
                "pose": {
                    "x": round(float(pose.position.x), 1),
                    "y": round(float(pose.position.y), 1),
                    "z": round(float(pose.position.z), 1),
                    "angle_deg": round(float(pose.rotation.angle_z.degrees), 1),
                },
                "notes": _state_notes(on_charger=on_charger, charging=charging, calm_power_mode=calm_power_mode),
                "ts": time.time(),
            }
    except Exception as exc:
        return {
            "connected": False,
            "sleeping": True,
            "notes": f"Live robot state read failed; treating autonomy as unsafe until the next good read. {exc}",
            "ts": time.time(),
        }


def _estimate_battery_percent(volts: Any) -> float | None:
    voltage = _optional_float(volts)
    if voltage is None:
        return None

    curve = _BATTERY_VOLTAGE_CURVE
    if voltage <= curve[0][0]:
        return float(curve[0][1])
    if voltage >= curve[-1][0]:
        return float(curve[-1][1])

    for (low_v, low_pct), (high_v, high_pct) in zip(curve, curve[1:]):
        if low_v <= voltage <= high_v:
            span = high_v - low_v
            if span <= 0:
                return float(low_pct)
            ratio = (voltage - low_v) / span
            return round(float(low_pct + ratio * (high_pct - low_pct)), 1)
    return None


def _is_low_battery(level: Any, volts: Any) -> bool:
    try:
        if level is not None and int(level) <= 1:
            return True
    except (TypeError, ValueError):
        pass

    percent = _estimate_battery_percent(volts)
    return percent is not None and percent < 20.0


def _state_notes(*, on_charger: bool, charging: bool, calm_power_mode: bool) -> str | None:
    notes: list[str] = []
    if on_charger:
        notes.append("Vector is on the charger.")
    if charging:
        notes.append("Vector is charging.")
    if calm_power_mode:
        notes.append("Vector is in calm power mode or asleep.")
    return " ".join(notes) if notes else None


def _radians_to_degrees(value: Any) -> float | None:
    radians = _optional_float(value)
    if radians is None:
        return None
    return round(math.degrees(radians), 1)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_visible_faces(robot: Any) -> bool:
    try:
        faces = getattr(getattr(robot, "world", None), "visible_faces", None)
        return bool(faces)
    except Exception:
        return False


def _has_connected_cube(robot: Any) -> bool:
    try:
        cube = getattr(getattr(robot, "world", None), "connected_light_cube", None)
        if cube is None:
            return False
        return bool(getattr(cube, "is_visible", True))
    except Exception:
        return False


def _capture_view_sync(serial: str, output_dir: Path) -> dict[str, Any]:
    try:
        import anki_vector

        with anki_vector.Robot(
            serial=serial,
            behavior_control_level=None,
            default_logging=False,
        ) as robot:
            _prepare_camera_view(robot)
            raw_image = _capture_view_raw_grpc(robot, timeout=6)
    except Exception as raw_exc:
        try:
            import anki_vector

            with anki_vector.Robot(
                serial=serial,
                behavior_control_level=None,
                default_logging=False,
            ) as robot:
                control_future = robot.conn.request_control(timeout=5)
                if hasattr(control_future, "result"):
                    control_future.result(timeout=6)
                try:
                    _set_camera_head_angle(robot)
                    time.sleep(0.25)
                    raw_image = _capture_view_raw_grpc(robot, timeout=6)
                finally:
                    try:
                        release_future = robot.conn.release_control(timeout=2)
                        if hasattr(release_future, "result"):
                            release_future.result(timeout=3)
                    except Exception:
                        pass
        except Exception as control_exc:
            raw_detail = str(raw_exc) or repr(raw_exc)
            control_detail = str(control_exc) or repr(control_exc)
            return {
                "ok": False,
                "error": f"Camera capture failed: raw={raw_detail}; with_control={control_detail}",
                "ts": time.time(),
            }

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "latest.jpg"
        temp_path = output_dir / f"latest.{time.time_ns()}.tmp.jpg"
        raw_image.save(temp_path, format="JPEG", quality=88)
        temp_path.replace(path)
        buffer = io.BytesIO()
        raw_image.save(buffer, format="JPEG", quality=88)
        brightness = _average_brightness(raw_image)
        return {
            "ok": True,
            "path": str(path),
            "width": raw_image.width,
            "height": raw_image.height,
            "brightness": brightness,
            "dark_frame": brightness is not None and brightness < 18.0,
            "image_b64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        }
    except Exception as exc:
        detail = str(exc) or repr(exc)
        return {
            "ok": False,
            "error": f"Camera capture failed: {detail}",
            "ts": time.time(),
        }


def _capture_view_raw_grpc(robot: Any, *, timeout: float) -> Any:
    from anki_vector.messaging import protocol

    async def capture_single_image():
        request = protocol.CaptureSingleImageRequest(enable_high_resolution=False)
        return await robot.conn.grpc_interface.CaptureSingleImage(request)

    response = robot.conn.run_coroutine(capture_single_image()).result(timeout=timeout)
    if not response or not response.data:
        raise RuntimeError("empty raw camera response")
    return Image.open(io.BytesIO(response.data)).convert("RGB")


def _prepare_camera_view(robot: Any) -> None:
    got_control = False
    try:
        control_future = robot.conn.request_control(timeout=3)
        if hasattr(control_future, "result"):
            control_future.result(timeout=5)
        got_control = True
        _set_camera_head_angle(robot)
        time.sleep(0.25)
    except Exception:
        pass
    finally:
        if got_control:
            try:
                release_future = robot.conn.release_control(timeout=2)
                if hasattr(release_future, "result"):
                    release_future.result(timeout=3)
            except Exception:
                pass


def _set_camera_head_angle(robot: Any) -> None:
    from anki_vector.messaging import protocol

    req = protocol.SetHeadAngleRequest(
        angle_rad=math.radians(18),
        max_speed_rad_per_sec=8.0,
        accel_rad_per_sec2=10.0,
        duration_sec=0.0,
        id_tag=int(getattr(protocol, "FIRST_SDK_TAG", 2000001)) + 500,
        num_retries=0,
    )
    robot.conn.run_coroutine(robot.conn.grpc_interface.SetHeadAngle(req)).result(timeout=4)


def _average_brightness(image: Any) -> float | None:
    try:
        from PIL import ImageStat

        stat = ImageStat.Stat(image.convert("L"))
        return round(float(stat.mean[0]), 1)
    except Exception:
        return None


def _vision_models(model: str) -> list[str]:
    models = [item.strip() for item in model.split(",") if item.strip()]
    return models or ["moondream"]


async def _describe_with_fallbacks(
    *,
    image_b64: str,
    ollama_base_url: str,
    models: list[str],
    prompt: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    last_result: dict[str, Any] | None = None
    for model in models:
        result = await _describe_image(
            image_b64=image_b64,
            ollama_base_url=ollama_base_url,
            model=model,
            prompt=prompt,
        )
        last_result = result
        if result.get("description"):
            if errors:
                result["vision_fallback_errors"] = errors
            return result
        if result.get("vision_error"):
            errors.append(f"{model}: {result['vision_error']}")
        else:
            errors.append(f"{model}: empty response")

    if last_result is None:
        return {"ok": True, "description": None, "vision_model": None, "vision_error": "No vision models configured."}
    if errors:
        last_result["vision_error"] = " | ".join(errors)
    return last_result


async def _describe_image(
    *,
    image_b64: str,
    ollama_base_url: str,
    model: str,
    prompt: str | None = None,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "prompt": prompt or _vision_prompt_for_model(model),
        "images": [image_b64],
        "stream": False,
        "options": {"temperature": 0.15, "num_predict": 110},
    }
    try:
        data = await ollama_generate(ollama_base_url, payload, timeout=45)
    except Exception as exc:
        return {
            "ok": True,
            "description": None,
            "vision_model": model,
            "vision_error": f"Vision model failed: {exc}",
        }
    return {
        "ok": True,
        "description": _compact_description(data.get("response") or ""),
        "vision_model": model,
    }


def _vision_prompt_for_model(model: str) -> str:
    # All models get the same targeted prompt — moondream handles it fine.
    return (
        "You are Pip's tiny visual cortex. Describe the scene from the robot's low camera viewpoint "
        "in 1-2 sentences under 45 words. "
        "Prioritize: people or faces, hands, the light cube, the charger dock, desk edges, "
        "hazards (cliff edges, drops), and any notable objects. "
        "Use spatial cues (left, center, right, near, far) when clear. "
        "Be honest about uncertainty — say 'unclear' rather than guessing."
    )


def _compact_description(text: str, *, max_words: int = 55, max_chars: int = 420) -> str:
    words = text.strip().split()
    if not words:
        return ""
    normalized = " ".join(words).strip(" !.")
    prompt_echoes = {
        "Pip's tiny visual cortex",
        "Pips tiny visual cortex",
    }
    if normalized in prompt_echoes:
        return ""
    compact = " ".join(words[:max_words])
    if len(compact) > max_chars:
        compact = compact[:max_chars].rsplit(" ", 1)[0]
    if len(words) > max_words or len(text) > len(compact):
        compact = compact.rstrip(" .,;:") + "."
    return compact
