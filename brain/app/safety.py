from __future__ import annotations

from .schemas import Action, RobotState, StopAction


EXPRESSIVE_ACTIONS = {"say", "head", "lift", "animation", "stop", "listen"}
MOTION_ACTIONS = {"drive", "turn"}
SAFE_BEHAVIORS = {
    "look_around",
    "find_faces",
    "connect_cube",
    "roll_visible_cube",
    "go_home",
    "drive_off_charger",
}
MOTION_BEHAVIORS = {"look_around", "roll_visible_cube", "go_home", "drive_off_charger"}
SAFE_ANIMATION_ALIASES = {
    "happy": "anim_onboarding_reacttoface_happy_01",
    "veryHappy": "anim_blackjack_victorwin_01",
    "sad": "anim_feedback_meanwords_01",
    "confused": "anim_meetvictor_lookface_timeout_01",
    "thinking": "anim_explorer_scan_short_04",
    "celebrate": "anim_pounce_success_03",
    "love": "anim_feedback_iloveyou_02",
}


def safety_filter(actions: list[Action], state: RobotState) -> tuple[list[Action], list[dict], list[str]]:
    safe: list[Action] = []
    denied: list[dict] = []
    notes: list[str] = []

    unsafe_motion_reason = _motion_block_reason(state)
    saw_stop = False

    for action in actions[:5]:
        action_type = action.type
        if action_type == "stop":
            saw_stop = True

        if action_type in MOTION_ACTIONS and unsafe_motion_reason:
            denied.append({"action": action.model_dump(), "reason": unsafe_motion_reason})
            continue

        if action_type == "behavior":
            if action.name not in SAFE_BEHAVIORS:
                denied.append({"action": action.model_dump(), "reason": "unknown native behavior"})
                continue
            if action.name in MOTION_BEHAVIORS and unsafe_motion_reason:
                denied.append({"action": action.model_dump(), "reason": unsafe_motion_reason})
                continue

        if action_type == "drive":
            if abs(action.speed_mmps) > 80 or action.duration_ms > 2000:
                denied.append({"action": action.model_dump(), "reason": "drive command exceeded hard safety cap"})
                continue

        if action_type == "turn" and abs(action.degrees) > 90:
            denied.append({"action": action.model_dump(), "reason": "turn command exceeded hard safety cap"})
            continue

        if action_type == "animation" and action.name not in SAFE_ANIMATION_ALIASES:
            denied.append({"action": action.model_dump(), "reason": "unknown animation alias"})
            continue

        safe.append(action)

    if safe and safe[-1].type == "listen":
        prior_actions = safe[:-1]
        if any(_is_motion_like(action) for action in prior_actions) and not any(
            action.type == "stop" for action in prior_actions
        ):
            safe.insert(-1, StopAction(type="stop"))
            notes.append("inserted stop before terminal listen action")
    elif not safe or safe[-1].type != "stop":
        safe.append(StopAction(type="stop"))
        notes.append("appended stop action")

    if saw_stop and safe[-1].type != "stop":
        notes.append("normalized stop to the end of the plan")

    if len(actions) > 5:
        notes.append("truncated plan to 5 actions")

    return safe[:6], denied, notes


def _motion_block_reason(state: RobotState) -> str | None:
    if state.connected is False:
        return "live robot state is unavailable"
    if state.sleeping or state.calm_power_mode:
        return "robot is asleep or in calm power mode"
    if state.charging:
        return "robot is charging"
    if state.picked_up:
        return "robot is picked up"
    if state.being_held:
        return "robot is being held"
    if state.cliff_detected:
        return "cliff detected"
    if state.obstacle_close:
        return "obstacle is close"
    if state.low_battery:
        return "battery is low"
    if state.on_charger:
        return "robot is on charger; motion disabled for planner test mode"
    return None


def _is_motion_like(action: Action) -> bool:
    if action.type in MOTION_ACTIONS:
        return True
    return action.type == "behavior" and action.name in MOTION_BEHAVIORS
