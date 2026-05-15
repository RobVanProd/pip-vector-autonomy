"""
goals.py — Pip's active mission / goal system.

Design intent
─────────────
Without a goal system, each autonomy tick is independent. Gemma picks
something to do without any sense of what it was doing 45 seconds ago.
The result is a robot that *feels* random even when each individual tick
is sensible.

A goal gives consecutive ticks a shared narrative thread:
  "Pip is investigating the thing that moved on the left. Tick 1 of 3."
  "Pip is still investigating. Tick 2 of 3."
  "Investigation complete. Returning to exploration."

This is what makes the Galaxy's Edge droids feel continuous — they're
*pursuing something*, not just reacting.

Goals are:
  • Rule-selected (not Gemma-selected)
  • Persistent across ticks via memory.json["goal"]
  • Injected into every idle prompt as a "current mission" block
  • Consumed (tick budget decremented) after each autonomy tick

Priority system
───────────────
Goals have integer priorities. Higher-priority goals can interrupt lower ones.
The engine only replaces the active goal if the new goal has strictly higher
priority, OR if the current goal's tick budget is exhausted.

Goals:
  EXPLORING      — scanning desk, looking around; always available (priority 1)
  WATCHING       — focused on Rob specifically (priority 5)
  INVESTIGATING  — novel visual element worth examining (priority 4)
  SOCIALIZING    — Rob is present/active (priority 7)
  CELEBRATING    — post-positive-interaction burst (priority 6)
  SEEKING_CHARGER— critical battery; dock now (priority 10)
  RESTING        — charging / calm mode (priority 9)

Integration points
──────────────────
  autonomy.py   — calls goal_engine.update() before building idle prompt
  autonomy.py   — calls goal_engine.tick_used() after each tick
  autonomy.py   — calls goal_engine.prompt_fragment() for Gemma context
  sentinel.py   — calls goal_engine.update(events=[...]) on interrupt events
  main.py       — exposes GET /goals/state and POST /goals/set

NOTE(claude): Goal transitions are deterministic rule evaluations, not LLM calls.
  The value is narrative coherence over multiple ticks, not intelligent goal-setting.
  Keep the transition logic simple and predictable.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import RobotState


# ── Goal definitions ───────────────────────────────────────────────────────────

@dataclass
class GoalDef:
    """
    Static definition of a goal type.
    One GoalDef exists per goal name — these are constants.
    """
    name: str
    priority: int
    default_tick_budget: int
    gemma_description: str      # what Gemma sees in the prompt fragment
    gemma_action_hint: str      # suggested action direction for Gemma


GOAL_DEFS: dict[str, GoalDef] = {
    "EXPLORING": GoalDef(
        name="EXPLORING",
        priority=1,
        default_tick_budget=8,
        gemma_description=(
            "Pip is scanning the desk environment — looking around, noticing things, "
            "staying curious about what's near. No specific target, just awareness."
        ),
        gemma_action_hint=(
            "Good choices: look_around behavior, slow head movement, quiet observation. "
            "Speak only if something concrete is noticed."
        ),
    ),
    "WATCHING": GoalDef(
        name="WATCHING",
        priority=5,
        default_tick_budget=4,
        gemma_description=(
            "Pip is watching for Rob specifically — focused attention toward the last "
            "known Rob direction, staying alert for movement."
        ),
        gemma_action_hint=(
            "Good choices: head tilt toward Rob, thinking animation, quiet alert posture. "
            "A short phrase acknowledging Rob is natural."
        ),
    ),
    "INVESTIGATING": GoalDef(
        name="INVESTIGATING",
        priority=4,
        default_tick_budget=3,
        gemma_description=(
            "Pip noticed something novel in the visual field and is investigating. "
            "This is a focused, curious examination of a specific thing."
        ),
        gemma_action_hint=(
            "Good choices: look_around, head tilt toward the item, confused or thinking animation, "
            "a short phrase about what was noticed."
        ),
    ),
    "SOCIALIZING": GoalDef(
        name="SOCIALIZING",
        priority=7,
        default_tick_budget=5,
        gemma_description=(
            "Rob is present and Pip is in social mode — maximizing engagement, "
            "being expressive, responding to the shared moment."
        ),
        gemma_action_hint=(
            "Good choices: direct speech to Rob, happy or excited animations, "
            "expressive head and lift movement. This is the time to be talkative."
        ),
    ),
    "CELEBRATING": GoalDef(
        name="CELEBRATING",
        priority=6,
        default_tick_budget=2,    # intentionally short — a burst, not a sustained state
        gemma_description=(
            "Pip just had a positive interaction and is in a brief celebration burst. "
            "This will fade naturally in 1-2 ticks."
        ),
        gemma_action_hint=(
            "Good choices: celebrate or veryHappy animation, a proud short phrase, "
            "lift raise + lower. Keep it to one joyful beat."
        ),
    ),
    "SEEKING_CHARGER": GoalDef(
        name="SEEKING_CHARGER",
        priority=10,
        default_tick_budget=99,   # lasts until docked or battery recovers
        gemma_description=(
            "Pip's battery is critically low and the priority is finding the charger. "
            "Everything else is secondary."
        ),
        gemma_action_hint=(
            "Good choices: go_home behavior, a brief 'going to charge' phrase, "
            "then stop. Do not waste energy on exploration."
        ),
    ),
    "RESTING": GoalDef(
        name="RESTING",
        priority=9,
        default_tick_budget=99,   # lasts until not charging
        gemma_description=(
            "Pip is resting — on the charger or in calm power mode. "
            "Minimal action is appropriate. This is quiet recovery time."
        ),
        gemma_action_hint=(
            "Good choices: very gentle head movement, a sleepy or content expression, "
            "or complete silence. No motion. No urgency."
        ),
    ),
}


# ── Active goal state ──────────────────────────────────────────────────────────

@dataclass
class GoalState:
    """
    The current active goal instance.
    Tracks runtime state (ticks used, when it started, why it was chosen).
    """
    name: str
    priority: int
    tick_budget: int
    ticks_used: int = 0
    trigger_event: str = "initial"
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def exhausted(self) -> bool:
        """True when this goal has consumed its full tick budget."""
        return self.ticks_used >= self.tick_budget

    @property
    def ticks_remaining(self) -> int:
        return max(0, self.tick_budget - self.ticks_used)


# ── GoalEngine ─────────────────────────────────────────────────────────────────

class GoalEngine:
    """
    Manages Pip's active mission.

    Usage in autonomy loop:
        events = sentinel.pop_events()
        goal_engine.update(robot_state, events, emotion_state)
        prompt_ctx = goal_engine.prompt_fragment()
        # ... run tick ...
        goal_engine.tick_used()

    Usage in sentinel (reactive event):
        goal_engine.update(robot_state, events=["FACE_APPEARED"], emotion_state="ALERT")
    """

    def __init__(self, memory_path: Path | None = None) -> None:
        self._memory_path = memory_path or Path(
            os.getenv("VECTOR_MEMORY_PATH", "memory.json")
        )
        self.active: GoalState = self._make_goal("EXPLORING", trigger="initial boot")
        self.load()

    # ── Public API ─────────────────────────────────────────────────────────────

    def update(
        self,
        robot_state: RobotState,
        events: list[str] | None = None,
        emotion_state: str | None = None,
    ) -> str | None:
        """
        Evaluate goal transition rules and switch active goal if warranted.

        Parameters
        ----------
        robot_state : RobotState
            Current live robot state.
        events : list[str], optional
            Sentinel events fired this cycle (e.g. ["FACE_APPEARED"]).
        emotion_state : str, optional
            Current mood from EmotionEngine (e.g. "ALERT").

        Returns
        -------
        str | None
            Transition description if goal changed, else None.
        """
        events = events or []

        # Check if current goal is exhausted (ran out of ticks)
        if self.active.exhausted:
            reason = f"{self.active.name} goal exhausted after {self.active.ticks_used} ticks"
            self._switch_to("EXPLORING", trigger=reason)
            self.save()
            return reason

        # Evaluate candidate goals in priority order
        candidate, trigger = self._evaluate_candidates(robot_state, events, emotion_state)
        if candidate and GOAL_DEFS[candidate].priority > self.active.priority:
            reason = f"higher-priority goal: {candidate} ({trigger})"
            self._switch_to(candidate, trigger=trigger or reason)
            self.save()
            return reason

        return None

    def tick_used(self) -> None:
        """
        Decrement the active goal's tick budget by one.
        Call this after each successful autonomy tick.
        """
        self.active.ticks_used += 1
        self.save()

    def notify_positive_interaction(self) -> None:
        """
        Signal that Rob just had a positive interaction with Pip.
        This can trigger a CELEBRATING goal if priority allows.
        Called by the chat/voice route handler in main.py.
        """
        if GOAL_DEFS["CELEBRATING"].priority > self.active.priority:
            self._switch_to("CELEBRATING", trigger="positive interaction with Rob")
            self.save()

    def prompt_fragment(self) -> str:
        """
        Return a 3-4 line text block for injection into the Gemma idle prompt.
        Tells Gemma what the current mission is without prescribing actions.
        """
        defn = GOAL_DEFS.get(self.active.name)
        if not defn:
            return f"Active mission: {self.active.name}"
        return (
            f"Active mission: {self.active.name} "
            f"(tick {self.active.ticks_used + 1} of {self.active.tick_budget})\n"
            f"{defn.gemma_description}\n"
            f"{defn.gemma_action_hint}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializable snapshot of current goal state."""
        return {
            **asdict(self.active),
            "ticks_remaining": self.active.ticks_remaining,
            "exhausted": self.active.exhausted,
        }

    def force_goal(self, goal_name: str, reason: str = "manual override") -> None:
        """
        Manually set the active goal — used by POST /goals/set for testing.
        Overrides priority rules.
        """
        if goal_name not in GOAL_DEFS:
            raise ValueError(f"Unknown goal: {goal_name!r}. Must be one of {list(GOAL_DEFS.keys())}")
        self._switch_to(goal_name, trigger=reason)
        self.save()

    # ── Persistence ────────────────────────────────────────────────────────────

    def save(self) -> None:
        """Write active goal to memory.json["goal"]."""
        try:
            data = _load_json(self._memory_path)
            data["goal"] = asdict(self.active)
            _save_json(self._memory_path, data)
        except Exception:
            pass  # non-fatal

    def load(self) -> None:
        """Restore active goal from memory.json["goal"] if it exists."""
        try:
            data = _load_json(self._memory_path)
            saved = data.get("goal", {})
            if saved.get("name") in GOAL_DEFS:
                self.active = GoalState(**{
                    k: v for k, v in saved.items()
                    if k in GoalState.__dataclass_fields__
                })
        except Exception:
            pass  # start fresh

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _make_goal(self, name: str, trigger: str = "") -> GoalState:
        defn = GOAL_DEFS[name]
        return GoalState(
            name=name,
            priority=defn.priority,
            tick_budget=defn.default_tick_budget,
            trigger_event=trigger,
        )

    def _switch_to(self, name: str, trigger: str = "") -> None:
        self.active = self._make_goal(name, trigger=trigger)

    def _evaluate_candidates(
        self,
        robot_state: RobotState,
        events: list[str],
        emotion_state: str | None,
    ) -> tuple[str | None, str | None]:
        """
        Evaluate all goal transition rules.
        Returns (goal_name, trigger_reason) for the highest-priority match,
        or (None, None) if nothing should change.

        Rules are ordered highest-to-lowest priority so first match wins.
        """
        battery = robot_state.battery_percent

        # ── Priority 10: Battery critical → seek charger ──────────────────────
        if battery is not None and battery < 15:
            if self.active.name != "SEEKING_CHARGER":
                return "SEEKING_CHARGER", f"battery critical at {battery:.0f}%"

        # ── Priority 9: Charging / calm mode → rest ───────────────────────────
        if robot_state.charging or robot_state.calm_power_mode:
            if self.active.name != "RESTING":
                return "RESTING", "robot is charging or in calm mode"

        # ── Priority 7: Face appeared → socialize ─────────────────────────────
        if "FACE_APPEARED" in events:
            if self.active.name not in {"SOCIALIZING", "CELEBRATING"}:
                return "SOCIALIZING", "face appeared in camera"

        # ── Priority 6: Positive interaction just happened → celebrate ─────────
        # NOTE: This is triggered externally via notify_positive_interaction().
        # The evaluate loop handles priority gating only.

        # ── Priority 5: Picked up → watch (for what Rob does) ─────────────────
        if "PICKED_UP" in events:
            if self.active.name not in {"SOCIALIZING", "CELEBRATING"}:
                return "WATCHING", "was just picked up by Rob"

        # ── Priority 4: Novel vision → investigate ────────────────────────────
        if "NOVEL_VISION" in events:
            if self.active.name not in {"SOCIALIZING", "INVESTIGATING"}:
                return "INVESTIGATING", "novel element appeared in visual field"

        # ── Priority 3: Battery recovered → stop seeking charger ──────────────
        if battery is not None and battery > 80:
            if self.active.name == "SEEKING_CHARGER":
                return "EXPLORING", "battery recovered above 80%"

        # ── No high-priority candidate ─────────────────────────────────────────
        return None, None


# ── Utilities ──────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
