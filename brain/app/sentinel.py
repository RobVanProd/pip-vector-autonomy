"""
sentinel.py — Event-driven interrupt loop for Pip.

Design intent
─────────────
The autonomy loop fires on a slow tick (every 45s). That's fine for idle
chatter, but droids don't wait 45 seconds to notice a face. The sentinel
runs as a parallel asyncio task, polling every 2s. When it detects a
significant state change — face appeared, robot picked up, battery critical,
cube found — it fires an *immediate* reactive Gemma call and updates the
emotion/goal engines in real time.

This is what creates the "something caught my attention" reflex. Without it,
Pip is perpetually in slow idle mode. With it, Pip can react to Rob's face
appearing within 2-3 seconds.

Architecture
────────────
  Sentinel asyncio task (every 2s)
      │
      ├── read_robot_snapshot()   (max_age=3s — fresher than autonomy's 8s)
      │
      ├── _detect_events(current_snapshot, previous_snapshot)
      │       compares bool fields + battery thresholds
      │       returns list[str] of event names
      │
      ├── for each event (if not in cooldown for that event type):
      │       ├── emotion_engine.update(robot_state, events=[event])
      │       ├── goal_engine.update(robot_state, events=[event])
      │       └── _fire_reactive_tick(event, robot_state)
      │               → _build_reactive_prompt(event, robot_state)
      │               → create_plan(...)
      │               → safety filter (via create_plan)
      │               → executor.execute(...)
      │               → add_event("sentinel_reactive", ...)
      │
      └── store snapshot as _last_snapshot

Cooldown system
───────────────
Each event type has an independent cooldown. After firing, that event
cannot fire again until its cooldown expires. This prevents rapid-fire
reactions if state oscillates (e.g. face flickers in/out of frame).

Event → cooldown (seconds):
  FACE_APPEARED      30s   (don't greet the same face every 30s)
  FACE_LOST          60s   (mention once, then let it go)
  PICKED_UP          10s   (quick to react, short cooldown)
  PUT_DOWN           15s   (re-settle time)
  CUBE_APPEARED      60s   (cube stays on desk, don't repeat)
  BATTERY_LOW       300s   (5 min between battery nags)
  BATTERY_CRITICAL  120s   (2 min — critical but not constant)
  OBSTACLE_APPEARED  20s   (obstacle check)

Integration points
──────────────────
  main.py    — instantiates Sentinel, passes emotion_engine + goal_engine
  main.py    — exposes GET /sentinel/status, POST /sentinel/start, POST /sentinel/stop
  autonomy.py — runs concurrently (they share robot state cache, not a lock)

NOTE(claude): The sentinel does NOT try to lock out the autonomy loop.
  Both can fire at the same time. The safety layer + executor handle
  concurrent calls gracefully (each is an atomic gRPC + TTS sequence).
  The goal here is reactive complementarity, not strict serialization.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .emotion import EmotionEngine
from .events import add_event
from .executors import get_executor
from .goals import GoalEngine
from .planner import create_plan
from .robot_io import read_robot_snapshot, snapshot_to_robot_state
from .schemas import (
    ExecuteRequest,
    PlanRequest,
    RobotState,
    SentinelConfig,
    SentinelStatus,
)


# ── Event definitions ──────────────────────────────────────────────────────────

SENTINEL_EVENTS: dict[str, dict[str, int]] = {
    "FACE_APPEARED":     {"cooldown_s": 30,  "priority": 8},
    "FACE_LOST":         {"cooldown_s": 60,  "priority": 3},
    "PICKED_UP":         {"cooldown_s": 10,  "priority": 9},
    "PUT_DOWN":          {"cooldown_s": 15,  "priority": 7},
    "CUBE_APPEARED":     {"cooldown_s": 60,  "priority": 5},
    "BATTERY_LOW":       {"cooldown_s": 300, "priority": 8},
    "BATTERY_CRITICAL":  {"cooldown_s": 120, "priority": 10},
    "OBSTACLE_APPEARED": {"cooldown_s": 20,  "priority": 6},
}

# Per-event reactive prompt fragments — what Gemma sees instead of an idle tick.
REACTIVE_PROMPTS: dict[str, str] = {
    "FACE_APPEARED": (
        "INTERRUPT EVENT: A face just appeared in Pip's camera view.\n"
        "React immediately and naturally. This is a live moment, not an idle tick.\n"
        "Do not narrate that you noticed something — just react with action.\n"
        "Suggested: turn toward, head tilt, greeting animation, short phrase if appropriate.\n"
        "One or two actions max, then stop."
    ),
    "FACE_LOST": (
        "INTERRUPT EVENT: The face Pip was watching has left the camera view.\n"
        "React naturally — a small acknowledgment is fine, or just return to scanning.\n"
        "Keep it subtle. One action max, then stop."
    ),
    "PICKED_UP": (
        "INTERRUPT EVENT: Pip is being picked up!\n"
        "React immediately — this is surprising and physical.\n"
        "Suggested: excited animation, a short startled or pleased phrase, head tilt.\n"
        "Do NOT drive or run behaviors while airborne. One or two actions, then stop."
    ),
    "PUT_DOWN": (
        "INTERRUPT EVENT: Pip was just put back down on the surface.\n"
        "React to the landing — re-orient, re-settle, maybe a small expression.\n"
        "Keep it brief. One action, then stop."
    ),
    "CUBE_APPEARED": (
        "INTERRUPT EVENT: The light cube appeared in Pip's visual field.\n"
        "React with curiosity or delight — cubes are interesting to Pip.\n"
        "Suggested: look_around or connect_cube behavior, curious phrase, head tilt.\n"
        "One or two actions, then stop."
    ),
    "BATTERY_LOW": (
        "INTERRUPT EVENT: Battery is getting low.\n"
        "A brief, in-character acknowledgment is appropriate — Pip feels this.\n"
        "Suggested: a short phrase about needing to charge, maybe a slight head droop.\n"
        "Keep it understated. One action, then stop."
    ),
    "BATTERY_CRITICAL": (
        "INTERRUPT EVENT: Battery is critically low — Pip needs the charger NOW.\n"
        "Communicate this urgency briefly, then trigger go_home.\n"
        "Suggested: a short urgent phrase, then go_home behavior, then stop.\n"
        "Do NOT waste energy on anything else."
    ),
    "OBSTACLE_APPEARED": (
        "INTERRUPT EVENT: An obstacle is detected close to Pip.\n"
        "React cautiously — stop any motion, re-orient, express mild concern.\n"
        "Suggested: head tilt, confused animation, maybe a short phrase.\n"
        "Do NOT drive. One action, then stop."
    ),
}


# ── Sentinel ───────────────────────────────────────────────────────────────────

class Sentinel:
    """
    Event-driven interrupt loop that watches robot state every 2s and fires
    immediate reactive Gemma calls when significant state changes are detected.

    Usage in main.py:
        sentinel = Sentinel(
            model=MODEL,
            ollama_base_url=OLLAMA_BASE_URL,
            execution_mode=EXECUTION_MODE,
            vector_serial=VECTOR_SERIAL,
            emotion_engine=emotion_engine,
            goal_engine=goal_engine,
        )
        # Start via POST /sentinel/start
        # Check via GET /sentinel/status
        # Stop via POST /sentinel/stop
    """

    def __init__(
        self,
        *,
        model: str,
        ollama_base_url: str,
        execution_mode: str,
        vector_serial: str | None,
        emotion_engine: EmotionEngine,
        goal_engine: GoalEngine,
    ) -> None:
        self.model = model
        self.ollama_base_url = ollama_base_url
        self.execution_mode = execution_mode
        self.vector_serial = vector_serial
        self.emotion_engine = emotion_engine
        self.goal_engine = goal_engine

        self.config = SentinelConfig()
        self._task: asyncio.Task | None = None
        self._last_snapshot: dict[str, Any] | None = None
        self._cooldowns: dict[str, float] = {}   # event_name → last_fired_ts
        self._events_fired: int = 0
        self._last_event: str | None = None
        self._last_event_ts: float | None = None
        self._last_error: str | None = None
        self._polls: int = 0
        self._pending_events: list[str] = []

        # Injected from main.py. Called after reactive speech when
        # config.listen_after_speech=True — opens the Whisper reply window
        # so Rob can respond and Gemma handles the turn.
        self.listen_callback: Callable[[], Awaitable[None]] | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    async def start(self, config: SentinelConfig) -> SentinelStatus:
        """Start the sentinel poll loop."""
        self.config = config.model_copy(update={"enabled": True})
        self._last_error = None
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())
        return self.status()

    async def stop(self) -> SentinelStatus:
        """Stop the sentinel poll loop."""
        self.config = self.config.model_copy(update={"enabled": False})
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        return self.status()

    def status(self) -> SentinelStatus:
        """Return current sentinel state."""
        cooldown_remaining = {
            event: max(0.0, round(ts + SENTINEL_EVENTS[event]["cooldown_s"] - time.time(), 1))
            for event, ts in self._cooldowns.items()
            if event in SENTINEL_EVENTS
        }
        return SentinelStatus(
            enabled=self.config.enabled,
            running=self._task is not None and not self._task.done(),
            poll_interval_seconds=self.config.poll_interval_seconds,
            dry_run=self.config.dry_run,
            allow_motion=self.config.allow_motion,
            listen_after_speech=self.config.listen_after_speech,
            polls=self._polls,
            events_fired=self._events_fired,
            last_event=self._last_event,
            last_event_ts=self._last_event_ts,
            cooldown_remaining=cooldown_remaining,
            last_error=self._last_error,
        )

    def pop_events(self) -> list[str]:
        """
        Return and clear events detected in the most recent poll pass.
        Used by the autonomy loop to pass sentinel events into emotion/goal engines
        on the same cycle, avoiding double-processing.

        NOTE(claude): This is a simple drain — events are a list consumed once.
        The sentinel already updates emotion/goal engines directly when firing
        reactive ticks. pop_events() is for the autonomy loop's own update call.
        """
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    # ── Internal poll loop ─────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        while self.config.enabled:
            try:
                await self._poll_once()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
            await asyncio.sleep(self.config.poll_interval_seconds)

    async def _poll_once(self) -> None:
        self._polls += 1
        snapshot = await read_robot_snapshot(
            self.vector_serial,
            max_age_seconds=3,   # fresher than autonomy's 8s cache
        )
        robot_state = snapshot_to_robot_state(snapshot)

        if self._last_snapshot is not None:
            events = self._detect_events(snapshot, self._last_snapshot, robot_state)
            new_events = [e for e in events if self._event_allowed(e)]

            if new_events:
                # Sort by priority (highest first) so the most important fires first
                new_events.sort(key=lambda e: SENTINEL_EVENTS.get(e, {}).get("priority", 0), reverse=True)

                # Update engines with all new events
                self.emotion_engine.update(robot_state, new_events)
                self.goal_engine.update(robot_state, new_events, self.emotion_engine.state)

                # Accumulate for autonomy loop to read
                self._pending_events.extend(new_events)

                # Fire reactive ticks (highest-priority event only to avoid spamming)
                top_event = new_events[0]
                self._mark_cooldowns(new_events)
                await self._fire_reactive_tick(top_event, robot_state)

                add_event("sentinel_poll", {
                    "polls": self._polls,
                    "events_detected": events,
                    "events_fired": new_events,
                })
            # else: routine poll, no events — don't spam the event log

        self._last_snapshot = snapshot

    # ── Event detection ────────────────────────────────────────────────────────

    def _detect_events(
        self,
        curr: dict[str, Any],
        prev: dict[str, Any],
        robot_state: RobotState,
    ) -> list[str]:
        """
        Compare current snapshot to previous snapshot and return a list of
        event names for significant state changes.

        NOTE(claude): Events are edges, not levels. We only fire when state
        *changes*, not when it's merely in a particular state. This prevents
        the sentinel from re-firing the same event every 2s.
        """
        events: list[str] = []

        # ── Picked up / put down ──────────────────────────────────────────────
        curr_picked_up = bool(curr.get("picked_up") or curr.get("being_held"))
        prev_picked_up = bool(prev.get("picked_up") or prev.get("being_held"))
        if curr_picked_up and not prev_picked_up:
            events.append("PICKED_UP")
        elif not curr_picked_up and prev_picked_up:
            events.append("PUT_DOWN")

        # ── Face detection ────────────────────────────────────────────────────
        # Primary source: face_detected field (populated if SDK supports it)
        # Fallback: vision description text heuristic
        curr_face = bool(curr.get("face_detected")) or self._vision_mentions_face(curr)
        prev_face = bool(prev.get("face_detected")) or self._vision_mentions_face(prev)
        if curr_face and not prev_face:
            events.append("FACE_APPEARED")
        elif not curr_face and prev_face:
            events.append("FACE_LOST")

        # ── Cube detection ────────────────────────────────────────────────────
        curr_cube = bool(curr.get("cube_detected"))
        prev_cube = bool(prev.get("cube_detected"))
        if curr_cube and not prev_cube:
            events.append("CUBE_APPEARED")

        # ── Obstacle appeared ─────────────────────────────────────────────────
        curr_obstacle = bool(curr.get("obstacle_close") or curr.get("cliff_detected"))
        prev_obstacle = bool(prev.get("obstacle_close") or prev.get("cliff_detected"))
        if curr_obstacle and not prev_obstacle:
            events.append("OBSTACLE_APPEARED")

        # ── Battery thresholds ────────────────────────────────────────────────
        # Fire as edge: battery just crossed below threshold (not re-fire every poll)
        curr_pct = robot_state.battery_percent
        if curr_pct is not None:
            prev_pct = _try_float(prev.get("battery_percent"))
            if prev_pct is None:
                prev_pct = curr_pct  # no comparison possible

            if curr_pct < 10 and (prev_pct is None or prev_pct >= 10):
                events.append("BATTERY_CRITICAL")
            elif curr_pct < 20 and (prev_pct is None or prev_pct >= 20):
                events.append("BATTERY_LOW")

        return events

    def _vision_mentions_face(self, snapshot: dict[str, Any]) -> bool:
        """
        Heuristic: check if the most recent vision description mentions a face or person.
        Used as a fallback when the robot SDK doesn't provide face_detected directly.
        """
        description = snapshot.get("vision_description", "") or ""
        if not description:
            return False
        lower = description.lower()
        return any(kw in lower for kw in ("face", "person", "human", "man", "woman", "someone"))

    def _event_allowed(self, event: str) -> bool:
        """Return True if this event is not currently in cooldown."""
        last_fired = self._cooldowns.get(event, 0.0)
        cooldown = SENTINEL_EVENTS.get(event, {}).get("cooldown_s", 30)
        return (time.time() - last_fired) >= cooldown

    def _mark_cooldowns(self, events: list[str]) -> None:
        now = time.time()
        for event in events:
            self._cooldowns[event] = now
            self._events_fired += 1
            self._last_event = event
            self._last_event_ts = now

    # ── Reactive Gemma call ────────────────────────────────────────────────────

    async def _fire_reactive_tick(self, event: str, robot_state: RobotState) -> None:
        """
        Fire an immediate, targeted Gemma call in response to a sentinel event.
        This is not an idle tick — it's a live reaction prompt.
        """
        prompt = self._build_reactive_prompt(event, robot_state)
        try:
            plan = await create_plan(
                PlanRequest(user_text=prompt, robot_state=robot_state),
                model=self.model,
                ollama_base_url=self.ollama_base_url,
                execution_mode=self.execution_mode,
            )

            # Respect motion restrictions
            if not self.config.allow_motion:
                from .safety import MOTION_BEHAVIORS
                plan.actions = [
                    a for a in plan.actions
                    if a.type not in {"drive", "turn"}
                    and not (a.type == "behavior" and a.name in MOTION_BEHAVIORS)
                ]

            executor = get_executor(self.execution_mode, serial=self.vector_serial)
            result = await executor.execute(
                ExecuteRequest(
                    actions=plan.actions,
                    robot_state=robot_state,
                    dry_run=self.config.dry_run,
                )
            )

            say_text = " ".join(a.text for a in plan.actions if a.type == "say")
            add_event("sentinel_reactive", {
                "event": event,
                "emotion_state": self.emotion_engine.state,
                "goal_name": self.goal_engine.active.name,
                "actions": [a.model_dump() for a in plan.actions],
                "denied_actions": plan.denied_actions,
                "safety_notes": plan.safety_notes,
                "dry_run": self.config.dry_run,
                "executed": result.executed,
                "mode": result.mode,
                "raw": plan.raw,
            })

            # ── Turn-based conversation: open listen window after reactive speech ──
            # Only fire if Pip actually spoke and listen_after_speech is enabled.
            # Uses the same callback wired from main.py as the autonomy loop.
            if (
                say_text
                and self.config.listen_after_speech
                and self.listen_callback is not None
            ):
                try:
                    await self.listen_callback()
                except Exception as listen_exc:
                    add_event("sentinel_listen_error", {"event": event, "error": str(listen_exc)})

        except Exception as exc:
            self._last_error = f"reactive tick failed for {event}: {exc}"
            add_event("sentinel_error", {"event": event, "error": str(exc)})

    def _build_reactive_prompt(self, event: str, robot_state: RobotState) -> str:
        """
        Build the reactive Gemma prompt for a given event.
        Includes: event-specific prompt, current emotion/goal context, live state.
        """
        from .personality import time_context

        event_prompt = REACTIVE_PROMPTS.get(
            event,
            f"INTERRUPT EVENT: {event}. React naturally and briefly. One action, then stop."
        )
        emotion_fragment = self.emotion_engine.prompt_fragment()
        goal_fragment = self.goal_engine.prompt_fragment()

        return (
            f"You are Pip, a small local robot on Rob's desk.\n"
            f"{time_context()}\n\n"
            f"{event_prompt}\n\n"
            f"--- Current inner state ---\n"
            f"{emotion_fragment}\n"
            f"{goal_fragment}\n\n"
            f"Live robot state: {robot_state.model_dump_json(exclude_none=True)}\n\n"
            "This is a reactive moment, not a slow idle tick. "
            "Be immediate, specific, and in character. "
            "Keep it to 1-2 actions maximum, then stop."
        )


# ── Utilities ──────────────────────────────────────────────────────────────────

def _try_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
