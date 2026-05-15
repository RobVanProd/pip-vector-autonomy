from __future__ import annotations

import asyncio
import math
from typing import Protocol

from .personality import clean_say_text
from .robot_lock import SDK_LOCK
from .safety import SAFE_ANIMATION_ALIASES, safety_filter
from .schemas import Action, ExecuteRequest, ExecuteResponse


class Executor(Protocol):
    async def execute(self, req: ExecuteRequest) -> ExecuteResponse:
        ...


class MockExecutor:
    async def execute(self, req: ExecuteRequest) -> ExecuteResponse:
        actions, denied, notes = safety_filter(req.actions, req.robot_state)
        return ExecuteResponse(
            ok=True,
            mode="mock",
            executed=[action.model_dump() for action in actions],
            denied_actions=denied,
            safety_notes=notes,
        )


class VectorSdkExecutor:
    def __init__(self, serial: str | None = None, force_dry_run: bool = False) -> None:
        self.serial = serial
        self.force_dry_run = force_dry_run

    async def execute(self, req: ExecuteRequest) -> ExecuteResponse:
        actions, denied, notes = safety_filter(req.actions, req.robot_state)
        if req.dry_run or self.force_dry_run:
            return ExecuteResponse(
                ok=True,
                mode="vector-sdk-dry-run",
                executed=[action.model_dump() for action in actions],
                denied_actions=denied,
                safety_notes=notes,
            )

        try:
            async with SDK_LOCK:
                executed = await asyncio.wait_for(asyncio.to_thread(self._execute_sync, actions), timeout=45)
        except TimeoutError as exc:
            raise RuntimeError("Vector SDK execution timed out after 45 seconds") from exc
        return ExecuteResponse(
            ok=True,
            mode="vector-sdk",
            executed=executed,
            denied_actions=denied,
            safety_notes=notes,
        )

    def _execute_sync(self, actions: list[Action]) -> list[dict]:
        try:
            import anki_vector
            from anki_vector.connection import ControlPriorityLevel
            from anki_vector.messaging import protocol
        except ImportError as exc:
            raise RuntimeError(
                "Vector SDK is not installed. Install the wire-pod-compatible SDK before real execution."
            ) from exc

        executed: list[dict] = []
        robot_kwargs = {}
        if self.serial:
            robot_kwargs["serial"] = self.serial

        with anki_vector.Robot(
            **robot_kwargs,
            behavior_control_level=None,
            default_logging=False,
        ) as robot:
            next_action_id = int(getattr(protocol, "FIRST_SDK_TAG", 2000001))
            for action in actions:
                executed_action = action.model_dump()
                try:
                    if action.type == "say":
                        # Strip any bracketed stage cues ([chirp], [happy trill], etc.)
                        # that Gemma may have included as personality expression
                        tts_text = clean_say_text(action.text) or action.text
                        self._say_with_behavior_control(robot, tts_text)
                    elif action.type == "drive":
                        seconds = action.duration_ms / 1000
                        req = protocol.DriveStraightRequest(
                            speed_mmps=abs(float(action.speed_mmps)),
                            dist_mm=float(action.speed_mmps * seconds),
                            should_play_animation=False,
                            id_tag=next_action_id,
                            num_retries=0,
                        )
                        next_action_id += 1
                        robot.conn.run_coroutine(robot.conn.grpc_interface.DriveStraight(req)).result(timeout=8)
                    elif action.type == "turn":
                        req = protocol.TurnInPlaceRequest(
                            angle_rad=math.radians(action.degrees),
                            speed_rad_per_sec=math.radians(90),
                            accel_rad_per_sec2=math.radians(180),
                            tol_rad=math.radians(2),
                            is_absolute=0,
                            id_tag=next_action_id,
                            num_retries=0,
                        )
                        next_action_id += 1
                        robot.conn.run_coroutine(robot.conn.grpc_interface.TurnInPlace(req)).result(timeout=8)
                    elif action.type == "head":
                        req = protocol.SetHeadAngleRequest(
                            angle_rad=math.radians(action.angle_deg),
                            max_speed_rad_per_sec=10.0,
                            accel_rad_per_sec2=10.0,
                            duration_sec=0.0,
                            id_tag=next_action_id,
                            num_retries=0,
                        )
                        next_action_id += 1
                        robot.conn.run_coroutine(robot.conn.grpc_interface.SetHeadAngle(req)).result(timeout=8)
                    elif action.type == "lift":
                        lift_heights_mm = {"low": 32.0, "medium": 62.0, "high": 92.0}
                        req = protocol.SetLiftHeightRequest(
                            height_mm=lift_heights_mm[action.height],
                            max_speed_rad_per_sec=10.0,
                            accel_rad_per_sec2=10.0,
                            duration_sec=0.0,
                            id_tag=next_action_id,
                            num_retries=0,
                        )
                        next_action_id += 1
                        robot.conn.run_coroutine(robot.conn.grpc_interface.SetLiftHeight(req)).result(timeout=8)
                    elif action.type == "animation":
                        anim = protocol.Animation(name=SAFE_ANIMATION_ALIASES[action.name])
                        req = protocol.PlayAnimationRequest(
                            animation=anim,
                            loops=1,
                            ignore_body_track=True,
                            ignore_head_track=False,
                            ignore_lift_track=False,
                        )
                        robot.conn.run_coroutine(robot.conn.grpc_interface.PlayAnimation(req)).result(timeout=12)
                    elif action.type == "behavior":
                        self._execute_behavior(robot, action.name)
                    elif action.type == "listen":
                        # NOTE(claude 2026-05-14): ListenAction is intentionally a
                        # no-op in the SDK executor. The actual listening is handled
                        # by autonomy.py's listen_callback, which polls the Whisper
                        # listener (listener.py) after speech completes.
                        # We used to fire AppIntentRequest here (Vector's cloud STT)
                        # but that sent audio to Anki's servers. All STT now goes
                        # through the local Whisper pipeline instead.
                        pass
                    elif action.type == "stop":
                        req = protocol.StopAllMotorsRequest()
                        robot.conn.run_coroutine(robot.conn.grpc_interface.StopAllMotors(req)).result(timeout=5)
                except Exception as exc:
                    executed_action["error"] = str(exc) or repr(exc)
                executed.append(executed_action)

        return executed

    def _say_with_behavior_control(self, robot, text: str) -> None:
        try:
            robot.behavior.say_text(text, use_vector_voice=True, duration_scalar=1.0)
            return
        except Exception:
            pass

        last_error: Exception | None = None
        for timeout in (5, 10, 15):
            try:
                control_future = robot.conn.request_control(timeout=timeout)
                if hasattr(control_future, "result"):
                    control_future.result(timeout=timeout + 2)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            try:
                self._say_raw_grpc(robot, text)
                return
            except Exception as raw_exc:
                control_error = str(last_error) or repr(last_error)
                raw_error = str(raw_exc) or repr(raw_exc)
                raise RuntimeError(f"behavior-control speech failed: {control_error}; raw speech failed: {raw_error}") from raw_exc
        try:
            robot.behavior.say_text(text, use_vector_voice=True, duration_scalar=1.0)
        finally:
            try:
                release_future = robot.conn.release_control(timeout=2)
                if hasattr(release_future, "result"):
                    release_future.result(timeout=3)
            except Exception:
                pass

    def _say_raw_grpc(self, robot, text: str) -> None:
        """
        Last-resort TTS path: send SayTextRequest directly over gRPC
        without acquiring behavior control. Some firmware builds allow this.
        """
        from anki_vector.messaging import protocol
        req = protocol.SayTextRequest(
            text=text,
            use_vector_voice=True,
            duration_scalar=1.0,
        )
        robot.conn.run_coroutine(robot.conn.grpc_interface.SayText(req)).result(timeout=12)

    def _execute_behavior(self, robot, name: str) -> None:
        """
        Run a named Vector behavior via StartBehaviorRequest.
        These are native Anki behaviors — they use the robot's own motion planning.
        """
        from anki_vector.messaging import protocol
        behavior_map = {
            "look_around":         "LookAroundInPlace",
            "find_faces":          "FindFaces",
            "connect_cube":        "ConnectToCube",
            "roll_visible_cube":   "RollBlock",
            "go_home":             "GoHome",
            "drive_off_charger":   "DriveOffCharger",
        }
        behavior_name = behavior_map.get(name)
        if not behavior_name:
            raise ValueError(f"Unknown behavior: {name!r}")
        req = protocol.StartBehaviorRequest(behavior_type=behavior_name)
        robot.conn.run_coroutine(robot.conn.grpc_interface.StartBehavior(req)).result(timeout=20)


def get_executor(mode: str, *, serial: str | None = None) -> MockExecutor | VectorSdkExecutor:
    """
    Factory: return the right executor for the given execution mode.

    Modes:
      mock                 — logs only, no SDK calls (safe default)
      vector-sdk-dry-run   — connects SDK, reads state; no motion/speech
      vector-sdk           — full real execution on live robot
    """
    if mode == "mock":
        return MockExecutor()
    if mode == "vector-sdk-dry-run":
        return VectorSdkExecutor(serial=serial, force_dry_run=True)
    if mode == "vector-sdk":
        return VectorSdkExecutor(serial=serial, force_dry_run=False)
    # Unknown mode — default to mock so we never crash
    return MockExecutor()
