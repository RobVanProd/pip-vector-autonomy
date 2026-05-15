from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

MAX_OBSERVATIONS = 200


def map_path(capture_dir: Path) -> Path:
    return capture_dir / "environment-map.json"


def load_environment_map(capture_dir: Path) -> dict[str, Any]:
    path = map_path(capture_dir)
    if not path.exists():
        return {"version": 1, "observations": [], "updated_ts": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "observations": [], "updated_ts": None, "error": "map file could not be parsed"}
    if not isinstance(data, dict):
        return {"version": 1, "observations": [], "updated_ts": None}
    data.setdefault("version", 1)
    data.setdefault("observations", [])
    data.setdefault("updated_ts", None)
    return data


def record_environment_observation(
    capture_dir: Path,
    *,
    robot_state: dict[str, Any],
    external_view: dict[str, Any] | None,
    robot_view: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    data = load_environment_map(capture_dir)
    observation = {
        "ts": time.time(),
        "pose": robot_state.get("pose"),
        "head_angle_deg": robot_state.get("head_angle_deg"),
        "lift_height_mm": robot_state.get("lift_height_mm"),
        "battery_percent": robot_state.get("battery_percent"),
        "external_description": (external_view or {}).get("description"),
        "external_validation": (external_view or {}).get("validation"),
        "robot_description": (robot_view or {}).get("description"),
        "note": note,
    }
    observations = [item for item in data.get("observations", []) if isinstance(item, dict)]
    observations.append(observation)
    data["observations"] = observations[-MAX_OBSERVATIONS:]
    data["updated_ts"] = observation["ts"]
    data["summary"] = summarize_environment_map(data)
    capture_dir.mkdir(parents=True, exist_ok=True)
    map_path(capture_dir).write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def summarize_environment_map(data: dict[str, Any]) -> dict[str, Any]:
    observations = [item for item in data.get("observations", []) if isinstance(item, dict)]
    visible_counts = {"pip": 0, "cube": 0, "dock": 0}
    poses: list[dict[str, Any]] = []
    for item in observations:
        validation = item.get("external_validation") or {}
        if validation.get("pip_visible"):
            visible_counts["pip"] += 1
        if validation.get("cube_visible"):
            visible_counts["cube"] += 1
        if validation.get("dock_visible"):
            visible_counts["dock"] += 1
        pose = item.get("pose")
        if isinstance(pose, dict):
            poses.append(pose)
    return {
        "observation_count": len(observations),
        "visible_counts": visible_counts,
        "last_pose": poses[-1] if poses else None,
        "pose_count": len(poses),
    }
