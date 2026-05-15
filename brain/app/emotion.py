"""
emotion.py — Pip's persistent emotional state machine.

Design intent
─────────────
Pip always has a mood. That mood is not chosen by Gemma — it is determined
by a rule-based engine driven by real robot state (battery, events, time,
interaction history). Gemma receives the mood as grounding context and uses
it when deciding what to say and how to move.

This separation is intentional:
  • Gemma decides WHAT TO DO.
  • The emotion engine decides HOW PIP FEELS.

Rule-based transitions keep mood changes fast, predictable, and free of
hallucination. The emotion engine runs synchronously inside the autonomy
loop — no LLM calls.

States
──────
  CURIOUS   — default mode; exploration, head tilts, scanning
  CONTENT   — relaxed and satisfied; occasional positive expressions
  ALERT     — something noticed; heightened attention, less random motion
  PLAYFUL   — high energy; more speech, more motion, happy animations
  CAUTIOUS  — uncertain or wary; quieter, slower, confused animations
  TIRED     — low battery or late night; minimal action, quiet
  EXCITED   — face/cube/direct-interaction just happened; celebrate burst

Persistence
───────────
State is written to memory.json["emotion"] so it survives Docker restarts.
If no saved state exists, the engine starts in CURIOUS.

Integration points
──────────────────
  autonomy.py   — calls emotion_engine.update() before building idle prompt
  autonomy.py   — calls emotion_engine.prompt_fragment() for Gemma context
  autonomy.py   — uses emotion_engine.speak_probability_bias() to tune speech rate
  sentinel.py   — calls emotion_engine.update(events=[...]) on interrupt events
  main.py       — exposes GET /emotion/state and POST /emotion/set for inspection/testing

NOTE(claude): Keep transition logic deterministic. Do not add LLM calls here.
  The value of this module is speed and predictability — it runs on every tick.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import RobotState


# ── State definitions ──────────────────────────────────────────────────────────

class MoodState:
    CURIOUS  = "CURIOUS"
    CONTENT  = "CONTENT"
    ALERT    = "ALERT"
    PLAYFUL  = "PLAYFUL"
    CAUTIOUS = "CAUTIOUS"
    TIRED    = "TIRED"
    EXCITED  = "EXCITED"

    ALL = {CURIOUS, CONTENT, ALERT, PLAYFUL, CAUTIOUS, TIRED, EXCITED}


# ── Mood→behavior biases ───────────────────────────────────────────────────────
# Additive bias on top of the autonomy loop's base speak_probability.
# Keep values small so the base config still dominates.

MOOD_SPEAK_BIAS: dict[str, float] = {
    MoodState.CURIOUS:  0.00,   # neutral
    MoodState.CONTENT:  -0.05,  # slightly quieter — just being
    MoodState.ALERT:    +0.15,  # likely to comment on what it noticed
    MoodState.PLAYFUL:  +0.25,  # most talkative mood
    MoodState.CAUTIOUS: -0.10,  # reserved, watching
    MoodState.TIRED:    -0.20,  # very quiet
    MoodState.EXCITED:  +0.30,  # very talkative burst
}

# When picking an idle fallback animation, prefer these per mood.
MOOD_PREFERRED_ANIMATIONS: dict[str, list[str]] = {
    MoodState.CURIOUS:  ["thinking", "confused"],
    MoodState.CONTENT:  ["happy"],
    MoodState.ALERT:    ["thinking"],
    MoodState.PLAYFUL:  ["happy", "veryHappy", "celebrate"],
    MoodState.CAUTIOUS: ["confused"],
    MoodState.TIRED:    ["sad"],
    MoodState.EXCITED:  ["veryHappy", "celebrate", "love"],
}

# Human-readable description of each mood state for the Gemma prompt fragment.
MOOD_DESCRIPTIONS: dict[str, str] = {
    MoodState.CURIOUS:  "Pip is in a curious, exploratory mindset — scanning, tilting, wondering.",
    MoodState.CONTENT:  "Pip is feeling calm and satisfied. Quiet presence is natural.",
    MoodState.ALERT:    "Something caught Pip's attention. Heightened focus, less random motion.",
    MoodState.PLAYFUL:  "Pip is feeling energetic and expressive. More speech and movement feel natural.",
    MoodState.CAUTIOUS: "Pip is feeling uncertain or wary. Slower movements, quieter, watching.",
    MoodState.TIRED:    "Pip is tired — low battery or late night. Minimal action, very quiet.",
    MoodState.EXCITED:  "Pip just had an exciting moment. A short burst of celebration is in character.",
}

# Per-mood animation hint for Gemma (injected into prompt fragment).
MOOD_ANIMATION_HINT: dict[str, str] = {
    MoodState.CURIOUS:  "Prefer: thinking or confused animations.",
    MoodState.CONTENT:  "Prefer: happy animations.",
    MoodState.ALERT:    "Prefer: thinking animation, head tilts. Avoid celebrate.",
    MoodState.PLAYFUL:  "Prefer: happy, veryHappy, or celebrate animations.",
    MoodState.CAUTIOUS: "Prefer: confused animation. Avoid celebrate or love.",
    MoodState.TIRED:    "Prefer: sad animation. Minimal motion.",
    MoodState.EXCITED:  "Prefer: veryHappy, celebrate, or love animations.",
}

# Minimum ticks to stay in a state before natural decay applies.
# Prevents rapid flickering between states.
MOOD_MIN_TICKS: dict[str, int] = {
    MoodState.CURIOUS:  3,
    MoodState.CONTENT:  4,
    MoodState.ALERT:    2,
    MoodState.PLAYFUL:  3,
    MoodState.CAUTIOUS: 3,
    MoodState.TIRED:    5,
    MoodState.EXCITED:  2,   # short burst by design
}

# After this many ticks with no event, mood decays toward CURIOUS (baseline).
MOOD_DECAY_TICKS: dict[str, int] = {
    MoodState.CURIOUS:  999,  # baseline — never decays
    MoodState.CONTENT:  12,
    MoodState.ALERT:    4,
    MoodState.PLAYFUL:  6,
    MoodState.CAUTIOUS: 8,
    MoodState.TIRED:    999,  # only lifted by explicit trigger (battery recovery)
    MoodState.EXCITED:  3,    # short — decays back to CONTENT or CURIOUS
}


# ── EmotionEngine ──────────────────────────────────────────────────────────────

class EmotionEngine:
    """
    Manages Pip's persistent emotional state.

    Usage in autonomy loop:
        events = sentinel.pop_events()          # list of event strings
        emotion_engine.update(robot_state, events)
        speak_bias = emotion_engine.speak_probability_bias()
        prompt_ctx  = emotion_engine.prompt_fragment()

    Usage in sentinel:
        emotion_engine.update(robot_state, events=["FACE_APPEARED"])
    """

    def __init__(self, memory_path: Path | None = None) -> None:
        self._memory_path = memory_path or Path(
            os.getenv("VECTOR_MEMORY_PATH", "memory.json")
        )
        self.state: str = MoodState.CURIOUS
        self.ticks_in_state: int = 0
        self.last_transition_reason: str = "initial boot"
        self.updated_at: str = _now_iso()
        self.load()

    # ── Public API ─────────────────────────────────────────────────────────────

    def update(
        self,
        robot_state: RobotState,
        events: list[str] | None = None,
    ) -> str | None:
        """
        Evaluate transition rules and move to a new state if warranted.

        Parameters
        ----------
        robot_state : RobotState
            Current live robot state snapshot.
        events : list[str], optional
            Event strings fired by the sentinel this cycle.
            Examples: ["FACE_APPEARED", "PICKED_UP"]

        Returns
        -------
        str | None
            Human-readable transition reason if the state changed, else None.
        """
        events = events or []
        self.ticks_in_state += 1

        new_state, reason = self._evaluate_transitions(robot_state, events)
        if new_state and new_state != self.state:
            self.state = new_state
            self.ticks_in_state = 0
            self.last_transition_reason = reason or "rule triggered"
            self.updated_at = _now_iso()
            self.save()
            return reason

        # Natural decay — if in non-baseline mood too long, drift toward CURIOUS
        decay_limit = MOOD_DECAY_TICKS.get(self.state, 999)
        min_ticks = MOOD_MIN_TICKS.get(self.state, 3)
        if (
            self.ticks_in_state >= min_ticks
            and self.ticks_in_state >= decay_limit
            and self.state != MoodState.CURIOUS
            and self.state != MoodState.TIRED  # TIRED only lifted by explicit rule
        ):
            decay_target = MoodState.CONTENT if self.state == MoodState.EXCITED else MoodState.CURIOUS
            reason = f"natural decay after {self.ticks_in_state} ticks in {self.state}"
            self.state = decay_target
            self.ticks_in_state = 0
            self.last_transition_reason = reason
            self.updated_at = _now_iso()
            self.save()
            return reason

        return None

    def speak_probability_bias(self) -> float:
        """Return the additive bias to apply to the autonomy loop's speak_probability."""
        return MOOD_SPEAK_BIAS.get(self.state, 0.0)

    def preferred_animations(self) -> list[str]:
        """Return preferred animation names for the current mood (for fallback actions)."""
        return MOOD_PREFERRED_ANIMATIONS.get(self.state, ["happy"])

    def prompt_fragment(self) -> str:
        """
        Return a 2-3 line text block for injection into the Gemma idle prompt.
        Tells Gemma how Pip feels without telling it what to do.
        """
        desc = MOOD_DESCRIPTIONS.get(self.state, "")
        anim_hint = MOOD_ANIMATION_HINT.get(self.state, "")
        return (
            f"Current mood: {self.state} (held for {self.ticks_in_state} ticks)\n"
            f"{desc}\n"
            f"{anim_hint}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializable snapshot of current emotion state."""
        return {
            "state": self.state,
            "ticks_in_state": self.ticks_in_state,
            "last_transition_reason": self.last_transition_reason,
            "updated_at": self.updated_at,
            "speak_bias": self.speak_probability_bias(),
            "preferred_animations": self.preferred_animations(),
        }

    def force_state(self, state: str, reason: str = "manual override") -> None:
        """Manually set the mood state — used by POST /emotion/set for testing."""
        if state not in MoodState.ALL:
            raise ValueError(f"Unknown mood state: {state!r}. Must be one of {MoodState.ALL}")
        self.state = state
        self.ticks_in_state = 0
        self.last_transition_reason = reason
        self.updated_at = _now_iso()
        self.save()

    # ── Persistence ────────────────────────────────────────────────────────────

    def save(self) -> None:
        """Write current emotion state to memory.json["emotion"]."""
        try:
            data = _load_json(self._memory_path)
            data["emotion"] = {
                "state": self.state,
                "ticks_in_state": self.ticks_in_state,
                "last_transition_reason": self.last_transition_reason,
                "updated_at": self.updated_at,
            }
            _save_json(self._memory_path, data)
        except Exception:
            pass  # non-fatal — in-memory state is still correct

    def load(self) -> None:
        """Restore emotion state from memory.json["emotion"] if it exists."""
        try:
            data = _load_json(self._memory_path)
            saved = data.get("emotion", {})
            if saved.get("state") in MoodState.ALL:
                self.state = saved["state"]
                self.ticks_in_state = int(saved.get("ticks_in_state", 0))
                self.last_transition_reason = saved.get("last_transition_reason", "loaded from disk")
                self.updated_at = saved.get("updated_at", _now_iso())
        except Exception:
            pass  # start fresh if memory is corrupt or missing

    # ── Transition logic ───────────────────────────────────────────────────────

    def _evaluate_transitions(
        self,
        robot_state: RobotState,
        events: list[str],
    ) -> tuple[str | None, str | None]:
        """
        Evaluate all transition rules in priority order.
        Returns (new_state, reason) or (None, None) if no transition fires.

        Rules are ordered highest-to-lowest priority.
        First matching rule wins.
        """
        battery = robot_state.battery_percent
        hour = datetime.now().hour

        # ── Priority 10: Critical battery ────────────────────────────────────
        if battery is not None and battery < 10:
            if self.state != MoodState.TIRED:
                return MoodState.TIRED, f"battery critical at {battery:.0f}%"

        # ── Priority 9: Being picked up ───────────────────────────────────────
        if "PICKED_UP" in events:
            return MoodState.EXCITED, "Pip was picked up"

        # ── Priority 8: Low battery ───────────────────────────────────────────
        if battery is not None and battery < 20:
            if self.state not in {MoodState.TIRED, MoodState.CAUTIOUS}:
                return MoodState.TIRED, f"battery low at {battery:.0f}%"

        # ── Priority 7: Battery recovered from low ────────────────────────────
        if battery is not None and battery > 80 and self.state == MoodState.TIRED:
            return MoodState.CONTENT, f"battery recovered to {battery:.0f}%"

        # ── Priority 7: Face appeared ─────────────────────────────────────────
        if "FACE_APPEARED" in events:
            # If already excited/playful, bump to EXCITED; otherwise ALERT
            if self.state in {MoodState.PLAYFUL, MoodState.CONTENT, MoodState.CURIOUS}:
                return MoodState.EXCITED, "face appeared in camera"
            return MoodState.ALERT, "face appeared in camera"

        # ── Priority 6: Cube appeared ─────────────────────────────────────────
        if "CUBE_APPEARED" in events:
            return MoodState.PLAYFUL, "light cube appeared"

        # ── Priority 6: Placed back down ──────────────────────────────────────
        if "PUT_DOWN" in events and self.state == MoodState.EXCITED:
            return MoodState.ALERT, "just put back down"

        # ── Priority 5: Battery moderate — can relax if was cautious ─────────
        if battery is not None and 35 <= battery <= 80:
            if self.state == MoodState.CAUTIOUS and self.ticks_in_state >= 4:
                return MoodState.CURIOUS, f"battery stable at {battery:.0f}%"

        # ── Priority 4: Late night nudge toward tired ─────────────────────────
        if hour >= 23 or hour < 5:
            if self.state in {MoodState.CURIOUS, MoodState.CONTENT} and self.ticks_in_state >= 5:
                return MoodState.TIRED, "late night, winding down"

        # ── Priority 3: Alert decay — nothing interesting happened ────────────
        if self.state == MoodState.ALERT and self.ticks_in_state >= MOOD_MIN_TICKS[MoodState.ALERT]:
            if "FACE_APPEARED" not in events and "CUBE_APPEARED" not in events:
                return MoodState.CURIOUS, "alert resolved — returning to baseline"

        # ── Priority 2: Low battery nudge toward cautious ─────────────────────
        if battery is not None and 20 <= battery < 35:
            if self.state in {MoodState.PLAYFUL, MoodState.EXCITED, MoodState.CURIOUS}:
                if self.ticks_in_state >= 3:
                    return MoodState.CAUTIOUS, f"battery getting low at {battery:.0f}%"

        # No transition
        return None, None


# ── Utilities ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
