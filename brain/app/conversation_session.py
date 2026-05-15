from __future__ import annotations

import asyncio
import random
import re
import time
import uuid
from enum import Enum
from typing import Any

from .events import add_event
from .executors import get_executor
from .memory import memory_context, remember_turn
from .planner import create_conversation_plan, create_plan
from .robot_io import read_robot_snapshot, snapshot_to_robot_state
from .schemas import (
    Action,
    ConversationSessionConfig,
    ConversationSessionStatus,
    ExecuteRequest,
    PlanRequest,
    RobotState,
    SayAction,
    StopAction,
)


class ConversationState(str, Enum):
    IDLE = "IDLE"
    ENGAGED = "ENGAGED"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    COOLDOWN = "COOLDOWN"


_EXPLICIT_COMMAND_KEYWORDS = {
    "drive", "move", "go", "forward", "backward", "reverse", "turn", "spin",
    "rotate", "left", "right", "stop", "dock", "charge", "home", "look",
    "find", "cube", "roll", "lift", "raise", "lower", "head", "tilt",
    "say", "speak", "tell", "announce", "play", "dance", "celebrate",
    "photo", "picture", "camera", "see", "describe",
}

_EXIT_PHRASES = {
    "stop", "sleep", "go to sleep", "that's all", "that is all",
    "nevermind", "never mind", "goodbye", "bye", "thanks bye",
    "thank you bye", "that's enough", "that is enough",
    "quiet", "be quiet", "shut up", "pause",
}

_CUES = (
    "Anything else?",
    "Like this?",
    "I'm listening.",
    "Keep going?",
    "Want more?",
)

_GOODBYES = (
    "Okay, Rob.",
    "Going quiet.",
    "I'll listen for Pip.",
)


class ConversationSession:
    def __init__(
        self,
        *,
        model: str,
        ollama_base_url: str,
        execution_mode: str,
        vector_serial: str | None,
        emotion_engine: Any,
        goal_engine: Any,
        listener: Any | None = None,
    ) -> None:
        self.model = model
        self.ollama_base_url = ollama_base_url
        self.execution_mode = execution_mode
        self.vector_serial = vector_serial
        self.emotion_engine = emotion_engine
        self.goal_engine = goal_engine
        self.listener = listener

        self.config = ConversationSessionConfig()
        self.state = ConversationState.IDLE
        self.session_id: str | None = None
        self.turns = 0
        self.engaged_at: float | None = None
        self.last_activity: float | None = None
        self.last_action_summary: str | None = None
        self.last_error: str | None = None

        self._lock = asyncio.Lock()
        self._listen_task: asyncio.Task | None = None

    def status(self) -> ConversationSessionStatus:
        return ConversationSessionStatus(
            enabled=self.config.enabled,
            state=self.state.value,
            session_id=self.session_id,
            turns=self.turns,
            engaged_at=self.engaged_at,
            last_activity=self.last_activity,
            last_action_summary=self.last_action_summary,
            last_error=self.last_error,
            silence_timeout_s=self.config.silence_timeout_s,
            cue_probability=self.config.cue_probability,
            goodbye_probability=self.config.goodbye_probability,
            allow_motion=self.config.allow_motion,
            require_explicit_command_for_motion=self.config.require_explicit_command_for_motion,
        )

    def update_config(self, config: ConversationSessionConfig) -> ConversationSessionStatus:
        self.config = config
        return self.status()

    def is_active(self) -> bool:
        return self.config.enabled and self.state != ConversationState.IDLE

    async def reset(self, reason: str = "manual reset") -> ConversationSessionStatus:
        if self.state != ConversationState.IDLE:
            await self._cooldown(reason, say_goodbye=False)
        return self.status()

    async def on_wake_word(
        self,
        text: str,
        payload: dict | None = None,
        *,
        execute: bool | None = None,
        dry_run: bool | None = None,
    ) -> dict:
        if not self.config.enabled:
            return {"ok": False, "ignored": True, "reason": "conversation session disabled"}

        clean = self._strip_wake_word(text)
        if not clean:
            clean = "Rob started a conversation."

        async with self._lock:
            if self.state == ConversationState.IDLE:
                self.session_id = str(uuid.uuid4())
                self.turns = 0
                self.engaged_at = time.time()
                self.last_activity = self.engaged_at
                self.last_action_summary = None
                self.last_error = None
                self.state = ConversationState.ENGAGED
                self._drain_listener_pending()
                self._start_listen_loop()
                try:
                    self.emotion_engine.force_state("EXCITED", reason="conversation engaged")
                except Exception:
                    pass
                add_event(
                    "conversation_start",
                    {"session_id": self.session_id, "text": clean, "source": payload},
                )

        await self._handle_turn(clean, source=payload, execute=execute, dry_run=dry_run)
        return {"ok": True, "session": self.status().model_dump()}

    async def on_transcript(self, text: str, payload: dict | None = None) -> dict:
        if not self.is_active():
            return {"ok": False, "ignored": True, "reason": "conversation idle"}

        clean = " ".join(text.strip().split())
        if not clean:
            return {"ok": False, "ignored": True, "reason": "empty transcript"}

        if self._is_exit_phrase(clean):
            await self._cooldown(f"exit phrase: {clean}", say_goodbye=True)
            return {"ok": True, "ended": True, "session": self.status().model_dump()}

        if self.state != ConversationState.ENGAGED:
            add_event(
                "conversation_transcript_ignored",
                {"state": self.state.value, "text": clean, "payload": payload},
            )
            return {"ok": False, "ignored": True, "reason": f"busy: {self.state.value}"}

        await self._handle_turn(clean, source=payload)
        return {"ok": True, "session": self.status().model_dump()}

    async def _handle_turn(
        self,
        text: str,
        *,
        source: dict | None = None,
        execute: bool | None = None,
        dry_run: bool | None = None,
    ) -> None:
        self.state = ConversationState.THINKING
        self.last_activity = time.time()
        self.turns += 1

        execute_actions = self.config.execute if execute is None else execute
        dry_run_actions = self.config.dry_run if dry_run is None else dry_run

        try:
            robot_state = await self._robot_state()
            is_command = self._is_explicit_robot_command(text)
            prompt = self._build_engaged_prompt(text, robot_state, is_command=is_command)
            plan_fn = create_plan if is_command else create_conversation_plan
            plan = await plan_fn(
                PlanRequest(user_text=prompt, robot_state=robot_state),
                model=self.model,
                ollama_base_url=self.ollama_base_url,
                execution_mode=self.execution_mode,
            )

            if is_command:
                plan.actions = self._repair_command_actions(plan.actions)
            else:
                plan.actions = self._conversation_only_actions(plan.actions)

            if not self.config.allow_motion:
                plan.actions = self._strip_motion_actions(plan.actions)
            elif self.config.require_explicit_command_for_motion and not is_command:
                plan.actions = self._strip_motion_actions(plan.actions)

            self.state = ConversationState.SPEAKING
            execute_response = None
            if execute_actions:
                executor = get_executor(self.execution_mode, serial=self.vector_serial)
                execute_response = await executor.execute(
                    ExecuteRequest(actions=plan.actions, robot_state=robot_state, dry_run=dry_run_actions)
                )

            say_text = " ".join(action.text for action in plan.actions if action.type == "say")
            if say_text:
                remember_turn(text, say_text)
                self.goal_engine.notify_positive_interaction()

            self.last_action_summary = self._summarize_actions(plan.actions)
            add_event(
                "conversation_turn",
                {
                    "session_id": self.session_id,
                    "turn": self.turns,
                    "user_text": text,
                    "is_command": is_command,
                    "robot_state": robot_state.model_dump(exclude_none=True),
                    "actions": [action.model_dump() for action in plan.actions],
                    "executed": execute_response.model_dump() if execute_response else None,
                    "raw": plan.raw,
                    "source": source,
                },
            )

            cue = self._conversational_cue() if execute_actions else None
            if cue:
                await self._execute_local_say(cue, robot_state=robot_state, dry_run=dry_run_actions)

            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc) or repr(exc)
            add_event(
                "conversation_error",
                {"session_id": self.session_id, "turn": self.turns, "text": text, "error": self.last_error},
            )
            try:
                robot_state = await self._robot_state()
                await self._execute_local_say("I missed that. Say it again?", robot_state=robot_state, dry_run=dry_run_actions)
            except Exception:
                pass
        finally:
            if self.state != ConversationState.IDLE:
                self.state = ConversationState.ENGAGED
                self.last_activity = time.time()
                self._start_listen_loop()

    async def _cooldown(self, reason: str, *, say_goodbye: bool) -> None:
        previous = {
            "session_id": self.session_id,
            "turns": self.turns,
            "duration_s": round(time.time() - self.engaged_at, 1) if self.engaged_at else None,
            "reason": reason,
        }
        self.state = ConversationState.COOLDOWN
        if say_goodbye and random.random() < self.config.goodbye_probability:
            try:
                robot_state = await self._robot_state()
                await self._execute_local_say(random.choice(_GOODBYES), robot_state=robot_state, dry_run=self.config.dry_run)
            except Exception:
                pass
        await asyncio.sleep(1.0)
        self.state = ConversationState.IDLE
        self.session_id = None
        self.turns = 0
        self.engaged_at = None
        self.last_activity = None
        self.last_action_summary = None
        add_event("conversation_end", previous)

    def _start_listen_loop(self) -> None:
        if self._listen_task is None or self._listen_task.done():
            self._listen_task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self) -> None:
        while self.is_active():
            if self.state == ConversationState.ENGAGED:
                transcript = await self._pop_listener_pending()
                if transcript:
                    text = str(transcript.get("text") or "").strip()
                    if text:
                        await self.on_transcript(text, transcript)
                        continue
                if self.last_activity and time.time() - self.last_activity >= self.config.silence_timeout_s:
                    await self._cooldown("silence timeout", say_goodbye=True)
                    return
            await asyncio.sleep(0.25)

    async def _pop_listener_pending(self) -> dict | None:
        if self.listener is None:
            return None
        return await self.listener.pop_pending()

    def _drain_listener_pending(self) -> None:
        if self.listener is None:
            return

        async def drain() -> None:
            drained = 0
            while await self.listener.pop_pending():
                drained += 1
            if drained:
                add_event("conversation_listen_drain", {"drained": drained})

        asyncio.create_task(drain())

    async def _robot_state(self) -> RobotState:
        snapshot = await read_robot_snapshot(self.vector_serial)
        return snapshot_to_robot_state(snapshot)

    async def _execute_local_say(self, text: str, *, robot_state: RobotState, dry_run: bool) -> None:
        executor = get_executor(self.execution_mode, serial=self.vector_serial)
        await executor.execute(
            ExecuteRequest(
                actions=[SayAction(type="say", text=text), StopAction(type="stop")],
                robot_state=robot_state,
                dry_run=dry_run,
            )
        )

    def _build_engaged_prompt(self, text: str, robot_state: RobotState, *, is_command: bool) -> str:
        return (
            f"Active conversation with Rob, turn {self.turns}.\n"
            f"Session id: {self.session_id}\n"
            f"Last action: {self.last_action_summary or 'none yet'}\n"
            f"Memory:\n{memory_context(max_turns=6, max_facts=20)}\n\n"
            f"Robot state JSON: {robot_state.model_dump_json(exclude_none=True)}\n"
            f"Rob's follow-up: {text!r}\n"
            "Continue naturally. You know the context from the last action and memory.\n"
            "Do not narrate what you just did. Do not say setup phrases.\n"
            "Keep speech brief unless Rob asks for detail.\n"
            "If Rob gives a concrete movement or robot-control command, obey within safety limits.\n"
            f"Explicit robot command detected: {is_command}.\n"
            "Return a JSON action plan ending with stop."
        )

    def _is_exit_phrase(self, text: str) -> bool:
        lowered = text.lower()
        return any(phrase in lowered for phrase in _EXIT_PHRASES)

    def _conversational_cue(self) -> str | None:
        if random.random() >= self.config.cue_probability:
            return None
        return random.choice(_CUES)

    def _strip_wake_word(self, text: str) -> str:
        clean = " ".join(text.strip().strip("\"").split())
        return re.sub(r"^(hey\s+)?(pip|vector)[,\s]+", "", clean, flags=re.IGNORECASE).strip() or clean

    def _is_explicit_robot_command(self, text: str) -> bool:
        words = set(re.findall(r"[a-zA-Z]+", text.lower()))
        return bool(words & _EXPLICIT_COMMAND_KEYWORDS)

    def _repair_command_actions(self, actions: list[Action]) -> list[Action]:
        repaired = [action for action in actions if action.type != "listen"]
        if not repaired or repaired[-1].type != "stop":
            repaired.append(StopAction(type="stop"))
        return repaired

    def _conversation_only_actions(self, actions: list[Action]) -> list[Action]:
        allowed = {"say", "animation", "head", "lift", "listen", "stop"}
        filtered = [action for action in actions if action.type in allowed]
        if not filtered or filtered[-1].type != "stop":
            filtered.append(StopAction(type="stop"))
        return filtered

    def _strip_motion_actions(self, actions: list[Action]) -> list[Action]:
        filtered = [action for action in actions if action.type not in {"drive", "turn", "behavior"}]
        if not filtered or filtered[-1].type != "stop":
            filtered.append(StopAction(type="stop"))
        return filtered

    def _summarize_actions(self, actions: list[Action]) -> str:
        parts: list[str] = []
        for action in actions:
            if action.type == "say":
                parts.append(f"say({action.text!r})")
            elif action.type == "drive":
                parts.append(f"drive:{action.speed_mmps}mmps/{action.duration_ms}ms")
            elif action.type == "turn":
                parts.append(f"turn:{action.degrees}")
            elif action.type == "head":
                parts.append(f"head:{action.angle_deg}")
            elif action.type == "lift":
                parts.append(f"lift:{action.height}")
            elif action.type == "animation":
                parts.append(f"animation:{action.name}")
            elif action.type == "behavior":
                parts.append(f"behavior:{action.name}")
            else:
                parts.append(action.type)
        return " -> ".join(parts)
