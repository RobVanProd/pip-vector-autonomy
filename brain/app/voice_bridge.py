from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Awaitable, Callable

from google.protobuf.json_format import MessageToDict

from .events import add_event
from .executors import get_executor
from .memory import remember_turn
from .planner import create_conversation_plan
from .robot_io import read_robot_snapshot, snapshot_to_robot_state
from .schemas import ExecuteRequest, PlanRequest, VoiceBridgeConfig, VoiceBridgeStatus


class VoiceBridge:
    def __init__(self, *, model: str, ollama_base_url: str, execution_mode: str, vector_serial: str | None) -> None:
        self.model = model
        self.ollama_base_url = ollama_base_url
        self.execution_mode = execution_mode
        self.vector_serial = vector_serial
        self.config = VoiceBridgeConfig()
        self.connected = False
        self.last_wake: dict | None = None
        self.last_intent: dict | None = None
        self.last_error: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        # Injected from main.py. Called after voice-bridge speech when
        # config.listen_after_speech=True — opens the Whisper reply window
        # so Rob can respond and Gemma handles the turn conversationally.
        self.listen_callback: Callable[[], Awaitable[None]] | None = None
        self.wake_callback: Callable[[str, dict, bool, bool], Awaitable[dict]] | None = None

    def status(self) -> VoiceBridgeStatus:
        return VoiceBridgeStatus(
            enabled=self.config.enabled,
            connected=self.connected,
            dry_run=self.config.dry_run,
            allow_motion=self.config.allow_motion,
            use_behavior_control=self.config.use_behavior_control,
            route_intents_to_gemma=self.config.route_intents_to_gemma,
            listen_after_speech=self.config.listen_after_speech,
            last_wake=self.last_wake,
            last_intent=self.last_intent,
            last_error=self.last_error,
        )

    async def start(self, config: VoiceBridgeConfig) -> VoiceBridgeStatus:
        self.config = config.model_copy(update={"enabled": True})
        self.last_error = None
        if not self.vector_serial:
            self.last_error = "VECTOR_SERIAL is not configured."
            self.config = self.config.model_copy(update={"enabled": False})
            return self.status()
        self._loop = asyncio.get_running_loop()
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="VectorVoiceBridge")
            self._thread.start()
        return self.status()

    async def stop(self) -> VoiceBridgeStatus:
        self.config = self.config.model_copy(update={"enabled": False})
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            await asyncio.to_thread(thread.join, 5)
        self._thread = None
        self.connected = False
        return self.status()

    def _run(self) -> None:
        try:
            import anki_vector
            from anki_vector.events import Events

            robot_kwargs: dict[str, Any] = {
                "serial": self.vector_serial,
                "default_logging": False,
            }
            if not self.config.use_behavior_control:
                robot_kwargs["behavior_control_level"] = None

            with anki_vector.Robot(**robot_kwargs) as robot:
                robot.events.subscribe(self._on_wake_word, Events.wake_word)
                robot.events.subscribe(self._on_user_intent, Events.user_intent)
                self.connected = True
                add_event(
                    "voice_bridge",
                    {
                        "status": "connected",
                        "use_behavior_control": self.config.use_behavior_control,
                    },
                )
                while not self._stop.wait(0.5):
                    pass
        except Exception as exc:
            self.connected = False
            self.last_error = str(exc)
            add_event("voice_bridge_error", {"error": str(exc)})
        finally:
            self.connected = False

    def _on_wake_word(self, _robot, event_type, event) -> None:
        payload = {
            "event_type": str(event_type),
            "event": _event_to_dict(event),
            "ts": time.time(),
        }
        self.last_wake = payload
        add_event("voice_wake", payload)

    def _on_user_intent(self, _robot, event_type, event) -> None:
        payload = self._intent_payload(event_type, event)
        self.last_intent = payload
        add_event("voice_intent", payload)
        if not self.config.route_intents_to_gemma or not self._loop:
            return
        asyncio.run_coroutine_threadsafe(self._handle_intent(payload), self._loop)

    async def _handle_intent(self, payload: dict) -> None:
        try:
            text = _intent_to_prompt(payload)
            if self.wake_callback is not None:
                await self.wake_callback(text, payload, self.config.dry_run, True)
                self.last_error = None
                return

            snapshot = await read_robot_snapshot(self.vector_serial)
            robot_state = snapshot_to_robot_state(snapshot)
            plan = await create_conversation_plan(
                PlanRequest(user_text=text, robot_state=robot_state),
                model=self.model,
                ollama_base_url=self.ollama_base_url,
                execution_mode=self.execution_mode,
            )
            plan.actions = [action for action in plan.actions if action.type != "listen"]
            if not self.config.allow_motion:
                plan.actions = [
                    action
                    for action in plan.actions
                    if action.type not in {"drive", "turn", "behavior"}
                ]

            say_text = " ".join(action.text for action in plan.actions if action.type == "say")
            if say_text:
                remember_turn(text, say_text)

            executor = get_executor(self.execution_mode, serial=self.vector_serial)
            result = await executor.execute(
                ExecuteRequest(actions=plan.actions, robot_state=robot_state, dry_run=self.config.dry_run)
            )
            add_event(
                "voice_gemma_execute",
                {
                    "voice": payload,
                    "prompt_text": text,
                    "robot_state": robot_state.model_dump(exclude_none=True),
                    "actions": [action.model_dump() for action in plan.actions],
                    "executed": result.executed,
                    "denied_actions": result.denied_actions,
                    "safety_notes": result.safety_notes,
                    "raw": plan.raw,
                    "model": plan.model,
                    "dry_run": self.config.dry_run,
                },
            )
            self.last_error = None

            # ── Turn-based conversation: open listen window after speech ──────
            # When listen_after_speech=True, open the Whisper reply window so
            # Rob can respond immediately after Pip speaks via the voice bridge.
            # Uses the same callback injected from main.py as the autonomy loop.
            if (
                say_text
                and self.config.listen_after_speech
                and self.listen_callback is not None
            ):
                try:
                    await self.listen_callback()
                except Exception as listen_exc:
                    add_event("voice_bridge_listen_error", {"error": str(listen_exc), "voice": payload})

        except Exception as exc:
            self.last_error = str(exc)
            add_event("voice_bridge_error", {"where": "handle_intent", "error": str(exc), "voice": payload})

    def _intent_payload(self, event_type, event) -> dict:
        event_dict = _event_to_dict(event)
        try:
            from anki_vector.user_intent import UserIntent

            user_intent = UserIntent(event)
            intent_name = user_intent.intent_event.name
            intent_data = user_intent.intent_data
        except Exception:
            intent_name = event_dict.get("intentId") or event_dict.get("intent_id") or "unknown"
            intent_data = event_dict.get("jsonData") or event_dict.get("json_data") or ""

        return {
            "event_type": str(event_type),
            "intent": intent_name,
            "intent_data": intent_data,
            "event": event_dict,
            "ts": time.time(),
        }


def _event_to_dict(event) -> dict:
    try:
        return MessageToDict(event, preserving_proto_field_name=True)
    except Exception:
        return {"repr": repr(event)}


def _intent_to_prompt(payload: dict) -> str:
    intent = str(payload.get("intent") or "unknown")
    intent_data = payload.get("intent_data") or ""
    for source in (intent_data, payload.get("event")):
        spoken = _spoken_text_from_data(source)
        if spoken:
            return spoken

    friendly = {
        "intent_greeting_hello": "Rob greeted Vector.",
        "greeting_hello": "Rob greeted Vector.",
        "intent_greeting_goodbye": "Rob said goodbye to Vector.",
        "greeting_goodbye": "Rob said goodbye to Vector.",
        "intent_imperative_affirmative": "Rob said yes.",
        "imperative_affirmative": "Rob said yes.",
        "intent_imperative_negative": "Rob said no.",
        "imperative_negative": "Rob said no.",
        "intent_imperative_love": "Rob told Vector he loves him.",
        "imperative_love": "Rob told Vector he loves him.",
        "intent_imperative_praise": "Rob praised Vector.",
        "imperative_praise": "Rob praised Vector.",
        "intent_imperative_scold": "Rob scolded Vector.",
        "imperative_scold": "Rob scolded Vector.",
        "intent_imperative_apology": "Rob apologized to Vector.",
        "imperative_apology": "Rob apologized to Vector.",
        "intent_imperative_dance": "Rob asked Vector to dance.",
        "imperative_dance": "Rob asked Vector to dance.",
        "intent_imperative_findcube": "Rob asked Vector to find his cube.",
        "imperative_findcube": "Rob asked Vector to find his cube.",
        "intent_imperative_fetchcube": "Rob asked Vector to fetch his cube.",
        "imperative_fetchcube": "Rob asked Vector to fetch his cube.",
        "intent_imperative_lookatme": "Rob asked Vector to look at him.",
        "imperative_lookatme": "Rob asked Vector to look at him.",
        "intent_knowledge_question": "Rob asked Vector a question, but this SDK did not provide the transcript.",
        "knowledge_question": "Rob asked Vector a question, but this SDK did not provide the transcript.",
    }
    return friendly.get(intent, f"Rob triggered: {intent}.")


def _spoken_text_from_data(source) -> str:
    """Extract spoken text string from wire-pod intent_data (JSON string or dict)."""
    if not source:
        return ""
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except Exception:
            return source.strip() if source.strip() else ""
    if isinstance(source, dict):
        for key in ("body", "utterance", "spoken_text", "transcription", "text"):
            val = source.get(key)
            if val and isinstance(val, str) and val.strip():
                return val.strip()
    return ""
