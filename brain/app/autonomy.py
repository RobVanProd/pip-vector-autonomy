from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from .emotion import EmotionEngine
from .events import add_event
from .executors import get_executor
from .goals import GoalEngine
from .memory import add_fact, load_memory, memory_context, recent_autonomous_says, remember_autonomous_say
from .personality import summarize_actions, time_context
from .planner import create_plan
from .robot_io import capture_and_describe_view, get_latest_vision, read_robot_snapshot, snapshot_to_robot_state
from .safety import MOTION_BEHAVIORS
from .schemas import (
    AnimationAction,
    AutonomyConfig,
    AutonomyStatus,
    ExecuteRequest,
    ExecuteResponse,
    HeadAction,
    ListenAction,
    PlanRequest,
    PlanResponse,
    RobotState,
    SayAction,
    StopAction,
)


class AutonomyLoop:
    def __init__(
        self,
        *,
        model: str,
        ollama_base_url: str,
        execution_mode: str,
        vector_serial: str | None,
        vision_model: str = "moondream",
        capture_dir: Path | None = None,
        emotion_engine: EmotionEngine | None = None,
        goal_engine: GoalEngine | None = None,
    ) -> None:
        self.model = model
        self.ollama_base_url = ollama_base_url
        self.execution_mode = execution_mode
        self.vector_serial = vector_serial
        self.vision_model = vision_model
        self.capture_dir = capture_dir or Path("captures").resolve()
        self.emotion_engine = emotion_engine or EmotionEngine()
        self.goal_engine = goal_engine or GoalEngine()
        self.config = AutonomyConfig()
        self.ticks = 0
        self.last_plan: PlanResponse | None = None
        self.last_execute: ExecuteResponse | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None
        self._last_vision_tick: int | None = None
        self._last_tick_summary: str | None = None   # what the previous tick did
        self._last_robot_state: RobotState = RobotState()

        # Injected from main.py after construction.
        # Called after autonomy speaks (listen_after_speech=True) to open a
        # reply window and route Rob's response back through Gemma.
        self.listen_callback: Callable[[], Awaitable[None]] | None = None

        # Memory consolidation state — tracks when we last consolidated so we
        # only ask Gemma when there's genuinely new conversation to process.
        self._consolidation_last_turn_count: int = 0
        self._consolidation_last_tick: int = 0

    def status(self) -> AutonomyStatus:
        return AutonomyStatus(
            enabled=self.config.enabled,
            dry_run=self.config.dry_run,
            interval_seconds=self.config.interval_seconds,
            allow_motion=self.config.allow_motion,
            listen_after_speech=self.config.listen_after_speech,
            respect_sleep=self.config.respect_sleep,
            include_vision=self.config.include_vision,
            vision_interval_ticks=self.config.vision_interval_ticks,
            speak_probability=self.config.speak_probability,
            vibe=self.config.vibe,
            ticks=self.ticks,
            last_plan=self.last_plan,
            last_execute=self.last_execute,
            last_error=self.last_error,
        )

    async def start(self, config: AutonomyConfig) -> AutonomyStatus:
        self.config = config.model_copy(update={"enabled": True})
        self.last_error = None
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return self.status()

    async def stop(self) -> AutonomyStatus:
        self.config = self.config.model_copy(update={"enabled": False})
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        return self.status()

    async def tick(self, config: AutonomyConfig | None = None) -> AutonomyStatus:
        if config is not None:
            self.config = config
        try:
            await self._tick_once()
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc) or repr(exc)
            add_event("autonomy_error", {"tick": self.ticks, "error": self.last_error, "source": "manual_tick"})
        return self.status()

    async def _run(self) -> None:
        while self.config.enabled:
            try:
                await self._tick_once()
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc) or repr(exc)
                add_event("autonomy_error", {"tick": self.ticks, "error": self.last_error, "source": "loop"})
            await asyncio.sleep(self._thermal_interval())

    async def _tick_once(self) -> None:
        self.ticks += 1
        snapshot = await read_robot_snapshot(self.vector_serial)
        robot_state = snapshot_to_robot_state(snapshot, self.config.robot_state)
        self._last_robot_state = robot_state

        if self.config.respect_sleep and self._should_skip_for_state(robot_state):
            self.last_plan = None
            self.last_execute = None
            add_event(
                "autonomy_skip",
                {
                    "tick": self.ticks,
                    "reason": "Vector is asleep, charging, on the charger, or live state is unavailable.",
                    "robot_state": robot_state.model_dump(exclude_none=True),
                },
            )
            return

        if await self._maybe_update_vision(robot_state):
            robot_state = snapshot_to_robot_state(snapshot, self.config.robot_state)

        # ── Emotion + goal update ─────────────────────────────────────────────
        # No sentinel events here — sentinel fires them on its own cycle.
        # Autonomy loop updates engines with current state only (no events).
        self.emotion_engine.update(robot_state, events=[])
        self.goal_engine.update(robot_state, events=[], emotion_state=self.emotion_engine.state)

        text = self._build_idle_prompt(robot_state)
        plan = await create_plan(
            PlanRequest(user_text=text, robot_state=robot_state),
            model=self.model,
            ollama_base_url=self.ollama_base_url,
            execution_mode=self.execution_mode,
        )

        if not self.config.allow_motion:
            plan.actions = [
                action
                for action in plan.actions
                if action.type not in {"drive", "turn"}
                and not (action.type == "behavior" and action.name in MOTION_BEHAVIORS)
            ]

        if self._needs_embodiment_fallback(plan):
            plan.actions = self._embodiment_fallback_actions(robot_state)
            plan.safety_notes.append("used local embodiment fallback because Gemma returned no usable action")

        plan.actions = self._maybe_add_listen_after_speech(plan.actions)

        add_event(
            "autonomy_plan",
            {
                "tick": self.ticks,
                "vibe": self.config.vibe,
                "robot_state": robot_state.model_dump(exclude_none=True),
                "actions": [action.model_dump() for action in plan.actions],
                "denied_actions": plan.denied_actions,
                "safety_notes": plan.safety_notes,
                "raw": plan.raw,
                "model": plan.model,
                "execution_mode": plan.execution_mode,
            },
        )

        executor = get_executor(self.execution_mode, serial=self.vector_serial)
        result = await executor.execute(
            ExecuteRequest(
                actions=plan.actions,
                robot_state=robot_state,
                dry_run=self.config.dry_run,
            )
        )
        add_event(
            "autonomy_execute",
            {
                "tick": self.ticks,
                "dry_run": self.config.dry_run,
                "executed": result.executed,
                "denied_actions": result.denied_actions,
                "safety_notes": result.safety_notes,
                "mode": result.mode,
            },
        )
        self.last_plan = plan
        self.last_execute = result

        # ── Post-tick memory and tracking ─────────────────────────────────────
        tick_summary = summarize_actions(plan.actions)
        self._last_tick_summary = tick_summary

        say_text = " ".join(action.text for action in plan.actions if action.type == "say")
        if say_text:
            # Store autonomous speech so next tick won't repeat it
            remember_autonomous_say(self.ticks, say_text, tick_summary)

        # Advance goal tick counter — one tick consumed
        self.goal_engine.tick_used()

        # ── Turn-based conversation: open a listen window after Pip speaks ────
        # If listen_after_speech is enabled and Pip said something this tick,
        # call the injected listen_callback so Rob can reply and Gemma handles it.
        # The callback is async and runs the full poll → transcribe → chat pipeline.
        if (
            say_text
            and self.config.listen_after_speech
            and self.listen_callback is not None
        ):
            try:
                await self.listen_callback()
            except Exception as exc:
                add_event("autonomy_listen_error", {"tick": self.ticks, "error": str(exc)})

        # ── Memory consolidation (every 10 ticks, if turns grew) ──────────────
        # Ask Gemma to extract durable facts from recent conversation and persist
        # them to memory.json["facts"]. This keeps the fact store fresh without
        # requiring Rob to manually curate it. Runs at most once every 10 ticks
        # and only when the conversation has grown since the last run.
        if self.ticks - self._consolidation_last_tick >= 10:
            await self._maybe_consolidate_memory()

    def _maybe_add_listen_after_speech(self, actions: list) -> list:
        if not self.config.listen_after_speech:
            return actions
        if not any(action.type == "say" and action.text.strip() for action in actions):
            return actions
        if any(action.type == "listen" for action in actions):
            return actions
        without_stop = [action for action in actions if action.type != "stop"]
        without_stop.append(ListenAction(type="listen", reason="Pip spoke autonomously; open a reply window"))
        return without_stop

    # ── Vision ─────────────────────────────────────────────────────────────────

    async def _maybe_update_vision(self, robot_state: RobotState) -> bool:
        if not self.config.include_vision:
            return False
        if (
            self._last_vision_tick is not None
            and self.ticks - self._last_vision_tick < self.config.vision_interval_ticks
        ):
            return False

        latest = get_latest_vision()
        latest_ts = latest.get("ts")
        if latest_ts and latest.get("description") and time.time() - float(latest_ts) < self.config.interval_seconds:
            self._last_vision_tick = self.ticks
            return True

        self._last_vision_tick = self.ticks
        result = await capture_and_describe_view(
            self.vector_serial,
            ollama_base_url=self.ollama_base_url,
            model=self.vision_model,
            output_dir=self.capture_dir,
        )
        add_event(
            "vision",
            {
                "tick": self.ticks,
                "robot_state": robot_state.model_dump(exclude_none=True),
                **result,
            },
        )
        return bool(result.get("description"))

    # ── State checks ───────────────────────────────────────────────────────────

    def _should_skip_for_state(self, robot_state: RobotState) -> bool:
        return bool(
            robot_state.connected is False
            or robot_state.sleeping
            or robot_state.charging
            or robot_state.calm_power_mode
            or robot_state.on_charger
        )

    def _thermal_interval(self) -> float:
        """
        Return a thermally-aware tick interval.
        When battery is critically low we slow the autonomy loop to 90s minimum
        to reduce SDK connection overhead and preserve charge.
        Below 20% → max(configured, 90s).
        Below 10% → max(configured, 180s).
        """
        base = self.config.interval_seconds
        batt = self._last_robot_state.battery_percent
        if batt is not None:
            if batt < 10:
                return max(base, 180.0)
            if batt < 20:
                return max(base, 90.0)
        return base

    async def _maybe_consolidate_memory(self) -> None:
        """
        Ask Gemma to extract durable facts from recent_turns and persist them
        to memory.json["facts"]. Called every 10 autonomy ticks.

        Design intent:
        - Only runs when recent_turns has grown since the last consolidation run
          (no new conversation → no new facts to extract)
        - Uses a strict, tight prompt that rejects transient states
        - Gemma may return an empty array — that's fine and expected often
        - Each extracted fact is deduped against existing facts before saving
        - Failure is silently logged and does not affect the main loop

        NOTE(claude 2026-05-14): We use a separate ultra-short prompt here
        (not the planner SYSTEM) so Gemma returns a plain JSON array, not
        an action plan. temperature=0.1 for deterministic, factual extraction.
        """
        self._consolidation_last_tick = self.ticks

        data = load_memory()
        turns = data.get("recent_turns", [])
        if len(turns) < 3:
            return  # not enough material
        current_turn_count = len(turns)
        if current_turn_count <= self._consolidation_last_turn_count:
            return  # nothing new since last run
        self._consolidation_last_turn_count = current_turn_count

        existing_facts: list[str] = data.get("facts", [])
        recent_turns = turns[-12:]  # last 12 turns — enough context, not too much
        transcript = "\n".join(
            f"Rob: {t.get('user', '')}\nPip: {t.get('assistant', '')}"
            for t in recent_turns
        )
        existing_str = "\n".join(f"- {f}" for f in existing_facts[:20]) or "- none"

        prompt = (
            "You are a memory extractor for Pip, a small robot on Rob's desk.\n"
            "Read the conversation below and extract 0-3 SHORT, durable facts about Rob or Pip's world.\n\n"
            "Rules:\n"
            "- Only facts that will still be true days from now (NOT 'Rob said hi just now')\n"
            "- Skip anything transient: moods, greetings, one-off events\n"
            "- Each fact must be under 20 words and concrete\n"
            "- Do NOT repeat facts already in the known list\n"
            "- Return ONLY a valid JSON array of strings: [\"fact 1\", \"fact 2\"]\n"
            "- If there are no new durable facts, return: []\n"
            "- No markdown, no explanation, just the JSON array\n\n"
            f"Known facts (do not repeat):\n{existing_str}\n\n"
            f"Recent conversation:\n{transcript}\n\n"
            "New durable facts:"
        )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 120},
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(f"{self.ollama_base_url}/api/generate", json=payload)
                response.raise_for_status()
            raw = (response.json().get("response") or "").strip()

            # Parse the JSON array — find first [ ... ] block
            start = raw.find("[")
            end = raw.rfind("]")
            if start == -1 or end == -1 or end < start:
                return
            new_facts: list = json.loads(raw[start : end + 1])
            if not isinstance(new_facts, list):
                return

            added = []
            for fact in new_facts:
                if isinstance(fact, str) and fact.strip():
                    result = add_fact(fact.strip())
                    added.append(fact.strip())

            if added:
                add_event("memory_consolidation", {
                    "tick": self.ticks,
                    "turns_processed": len(recent_turns),
                    "facts_added": added,
                })
        except Exception as exc:
            add_event("memory_consolidation_error", {
                "tick": self.ticks,
                "error": str(exc),
            })

    def _needs_embodiment_fallback(self, plan: PlanResponse) -> bool:
        if not plan.actions:
            return True
        if any(action.type != "stop" for action in plan.actions):
            return False
        return any("planner JSON parse failed" in note for note in plan.safety_notes) or not plan.raw.strip()

    # ── Fallback ───────────────────────────────────────────────────────────────

    def _embodiment_fallback_actions(self, robot_state: RobotState) -> list:
        actions: list = []
        if random.random() < self.config.speak_probability:
            actions.append(SayAction(type="say", text=self._fallback_phrase(robot_state)))
        actions.append(AnimationAction(type="animation", name=random.choice(["happy", "thinking", "confused"])))
        actions.append(HeadAction(type="head", angle_deg=random.choice([-8, 0, 8, 14])))
        actions.append(StopAction(type="stop"))
        return actions

    def _fallback_phrase(self, robot_state: RobotState) -> str:
        if robot_state.battery_percent is not None:
            return f"Rob, local brain is alive at {round(robot_state.battery_percent)} percent."
        return random.choice(
            [
                "Pip online, Rob.",
                "Tiny robot checking in.",
                "Local brain is running.",
                "Ready for a desk mission.",
            ]
        )

    # ── Idle prompt ────────────────────────────────────────────────────────────

    def _build_idle_prompt(self, robot_state: RobotState) -> str:
        # Apply emotion bias to speak probability (additive, clamped 0–1)
        effective_speak_prob = max(0.0, min(1.0,
            self.config.speak_probability + self.emotion_engine.speak_probability_bias()
        ))
        should_speak = random.random() < effective_speak_prob
        speech_rule = (
            "Include exactly one short say action with a concrete, in-character phrase "
            "(3-12 words). Do NOT use filler — if you have nothing grounded to say, "
            "return only silent actions."
            if should_speak
            else "Do NOT speak this tick — express yourself with silent actions only "
            "(head/lift/animation/stop). This is intentional pacing."
        )
        motion_rule = (
            "A tiny drive or turn is allowed if robot_state is clearly safe and off charger."
            if self.config.allow_motion
            else "Do not drive or turn."
        )

        # Vision context
        vision = get_latest_vision() if self.config.include_vision else {}
        vision_text = ""
        if vision.get("description"):
            age_s = time.time() - float(vision.get("ts", time.time()))
            age_str = f"{int(age_s)}s ago" if age_s < 120 else f"{int(age_s // 60)}m ago"
            vision_model = vision.get("vision_model") or "vision"
            vision_text = (
                f"Visual observation ({vision_model}, {age_str}): {vision['description']}\n"
            )

        # Previous tick — critical for avoiding repetition
        prev_tick_text = ""
        if self._last_tick_summary:
            prev_tick_text = f"Your previous tick action: {self._last_tick_summary}\n"

        # Recent idle phrases — repetition guard
        past_says = recent_autonomous_says(4)
        no_repeat_text = ""
        if past_says:
            phrases = "\n".join(f'  - "{p}"' for p in past_says)
            no_repeat_text = (
                f"Recent idle phrases (do NOT repeat or paraphrase these):\n{phrases}\n"
            )

        return (
            f"Idle embodiment tick #{self.ticks}. "
            f"You are Pip, a small local robot on Rob's desk.\n"
            f"{time_context()}\n"
            f"Vibe: {self.config.vibe}.\n\n"
            f"{self.emotion_engine.prompt_fragment()}\n"
            f"{self.goal_engine.prompt_fragment()}\n\n"
            f"Memory and context:\n{memory_context(max_turns=3, max_facts=10)}\n\n"
            f"Live robot state: {robot_state.model_dump_json(exclude_none=True)}\n"
            f"{vision_text}"
            f"{prev_tick_text}"
            f"{no_repeat_text}"
            "Choose a subtle, alive-feeling micro-behavior. Good idle choices:\n"
            "  - Scan the desk or react to Rob being nearby\n"
            "  - React to battery level, charger proximity, or cube presence\n"
            "  - Express a tiny mood (curious, proud, sleepy, alert)\n"
            "  - Do something expressive and completely silent\n"
            "Do NOT narrate safety compliance. Do NOT say you are 'keeping things safe'.\n"
            "Do NOT repeat filler phrases or variations of previous idle phrases.\n"
            "Use live body state, battery, pose, head angle, and vision as grounded context.\n"
            f"{speech_rule} {motion_rule}\n"
            "Keep it under 3 actions total and end with stop."
        )