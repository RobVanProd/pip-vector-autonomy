from __future__ import annotations

import asyncio
import base64
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from PIL import Image

from .robot_io import average_image_brightness, describe_image_b64

DEFAULT_CAMERA_NAME = "Logi C615 HD WebCam"
DEFAULT_VIDEO_SIZE = "1280x720"
LATEST_FILENAME = "external_latest.jpg"
EXTERNAL_CAMERA_PROMPT = (
    "You are validating Pip, a small black Anki Vector robot, from an external desk camera. "
    "Describe the scene in 1-2 concise sentences. Explicitly say whether the Vector/Pip robot "
    "is visible, including if it is only partially visible at an edge. Also mention the light cube, "
    "charger dock/base, desk edges, and hazards. Use words robot, cube, and dock when those objects are present."
)

_CAPTURE_LOCK = asyncio.Lock()
_LATEST_EXTERNAL_VIEW: dict[str, Any] = {}


def get_external_camera_config() -> dict[str, Any]:
    ffmpeg = os.getenv("VECTOR_EXTERNAL_CAMERA_FFMPEG") or shutil.which("ffmpeg")
    return {
        "camera_name": os.getenv("VECTOR_EXTERNAL_CAMERA_NAME", DEFAULT_CAMERA_NAME),
        "video_size": os.getenv("VECTOR_EXTERNAL_CAMERA_SIZE", DEFAULT_VIDEO_SIZE),
        "ffmpeg": ffmpeg,
        "ffmpeg_found": bool(ffmpeg),
    }


def get_latest_external_view() -> dict[str, Any]:
    return dict(_LATEST_EXTERNAL_VIEW)


def external_camera_status(capture_dir: Path) -> dict[str, Any]:
    config = get_external_camera_config()
    latest = get_latest_external_view()
    path = capture_dir / LATEST_FILENAME
    latest_age = None
    if latest.get("ts"):
        latest_age = round(time.time() - float(latest["ts"]), 1)
    return {
        **config,
        "latest_path": str(path) if path.exists() else None,
        "latest_age_seconds": latest_age,
        "latest": latest or None,
    }


async def capture_external_view(
    *,
    output_dir: Path,
    ollama_base_url: str,
    model: str,
    describe: bool = True,
    timeout_s: float = 20.0,
) -> dict[str, Any]:
    async with _CAPTURE_LOCK:
        capture = await asyncio.to_thread(_capture_external_view_sync, output_dir, timeout_s)

    if not capture.get("ok"):
        _remember_external_view(capture)
        return capture

    description_result: dict[str, Any] = {}
    if describe:
        description_result = await describe_image_b64(
            capture["image_b64"],
            ollama_base_url=ollama_base_url,
            model=model,
            prompt=EXTERNAL_CAMERA_PROMPT,
        )

    result = {
        **capture,
        **description_result,
        "image_b64": None,
        "validation": _validate_external_view(capture, description_result),
        "ts": time.time(),
    }
    _remember_external_view(result)
    return result


def _capture_external_view_sync(output_dir: Path, timeout_s: float) -> dict[str, Any]:
    config = get_external_camera_config()
    ffmpeg = config.get("ffmpeg")
    if not ffmpeg:
        return {
            "ok": False,
            "error": "ffmpeg is not on PATH. Set VECTOR_EXTERNAL_CAMERA_FFMPEG to its full path.",
            "ts": time.time(),
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    attempts = [
        (config["video_size"], 0.0),
        (config["video_size"], 2.0),
        ("640x480", 2.0),
    ]
    for attempt, (video_size, delay_s) in enumerate(attempts, start=1):
        if delay_s:
            time.sleep(delay_s)
        result = _run_ffmpeg_capture(
            ffmpeg=ffmpeg,
            camera_name=config["camera_name"],
            video_size=video_size,
            output_dir=output_dir,
            timeout_s=timeout_s,
            attempt=attempt,
        )
        if result.get("ok"):
            result["camera_name"] = config["camera_name"]
            result["video_size"] = video_size
            if errors:
                result["capture_fallback_errors"] = errors
            return result
        errors.append(str(result.get("error") or f"attempt {attempt} failed"))
    return {"ok": False, "error": " | ".join(errors), "ts": time.time()}


def _run_ffmpeg_capture(
    *,
    ffmpeg: str,
    camera_name: str,
    video_size: str,
    output_dir: Path,
    timeout_s: float,
    attempt: int,
) -> dict[str, Any]:
    latest_path = output_dir / LATEST_FILENAME
    temp_path = output_dir / f"external_latest.{time.time_ns()}.{attempt}.tmp.jpg"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "dshow",
        "-rtbufsize",
        "64M",
        "-video_size",
        video_size,
        "-i",
        f"video={camera_name}",
        "-frames:v",
        "1",
        "-update",
        "1",
        str(temp_path),
    ]

    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        _unlink_quietly(temp_path)
        return {"ok": False, "error": f"External camera capture timed out after {timeout_s:g}s.", "ts": time.time()}

    if completed.returncode != 0:
        _unlink_quietly(temp_path)
        detail = (completed.stderr or completed.stdout or "").strip()
        return {
            "ok": False,
            "error": f"External camera capture failed: {detail or 'ffmpeg returned a non-zero exit code.'}",
            "ts": time.time(),
        }

    try:
        with Image.open(temp_path) as image:
            rgb = image.convert("RGB")
            brightness = average_image_brightness(rgb)
            width, height = rgb.size
            with temp_path.open("rb") as handle:
                image_b64 = base64.b64encode(handle.read()).decode("ascii")
        temp_path.replace(latest_path)
    except Exception as exc:
        _unlink_quietly(temp_path)
        return {"ok": False, "error": f"External camera image validation failed: {exc}", "ts": time.time()}

    return {
        "ok": True,
        "path": str(latest_path),
        "width": width,
        "height": height,
        "brightness": brightness,
        "dark_frame": brightness is not None and brightness < 18.0,
        "image_b64": image_b64,
    }


def _validate_external_view(capture: dict[str, Any], description_result: dict[str, Any]) -> dict[str, Any]:
    description = str(description_result.get("description") or "").lower()
    image_ok = bool(capture.get("ok")) and not capture.get("dark_frame") and int(capture.get("width") or 0) >= 320
    vision_ok = bool(description_result.get("description")) and not description_result.get("vision_error")
    pip_visible = any(word in description for word in ("pip", "vector", "anki", "robot", "toy car", "small car"))
    dock_visible = any(word in description for word in ("charger", "dock", "charging", "base"))
    cube_visible = "cube" in description or "block" in description
    return {
        "ok": bool(image_ok and vision_ok and pip_visible),
        "image_ok": image_ok,
        "vision_ok": vision_ok,
        "pip_visible": pip_visible,
        "dock_visible": dock_visible,
        "cube_visible": cube_visible,
        "notes": _validation_notes(
            image_ok=image_ok,
            vision_ok=vision_ok,
            pip_visible=pip_visible,
            dock_visible=dock_visible,
            cube_visible=cube_visible,
        ),
    }


def _validation_notes(
    *,
    image_ok: bool,
    vision_ok: bool,
    pip_visible: bool,
    dock_visible: bool,
    cube_visible: bool,
) -> list[str]:
    notes: list[str] = []
    if not image_ok:
        notes.append("External camera frame is missing, too small, or too dark.")
    if not vision_ok:
        notes.append("Vision description did not return cleanly.")
    if not pip_visible:
        notes.append("Pip was not confidently detected in the external view.")
    if not dock_visible:
        notes.append("Dock/charger was not confidently detected in the external view.")
    if not cube_visible:
        notes.append("Cube/block was not confidently detected in the external view.")
    if not notes:
        notes.append("External camera sees Pip's area clearly enough for validation.")
    return notes


def _remember_external_view(result: dict[str, Any]) -> None:
    _LATEST_EXTERNAL_VIEW.clear()
    _LATEST_EXTERNAL_VIEW.update({k: v for k, v in result.items() if k != "image_b64"})


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
