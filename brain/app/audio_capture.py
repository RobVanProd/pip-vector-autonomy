from __future__ import annotations

import asyncio
import base64
import threading
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator


SERVICE_PATH = "/Anki.Vector.external_interface.ExternalInterface/AudioFeed"


@dataclass(slots=True)
class AudioFrame:
    robot_time_stamp: int
    group_id: int
    signal_power: bytes
    direction_strengths: bytes
    source_direction: int
    source_confidence: int
    noise_floor_power: int
    received_ts: float

    @classmethod
    def from_response(cls, response: Any) -> "AudioFrame":
        return cls(
            robot_time_stamp=int(response.robot_time_stamp),
            group_id=int(response.group_id),
            signal_power=bytes(response.signal_power),
            direction_strengths=bytes(response.direction_strengths),
            source_direction=int(response.source_direction),
            source_confidence=int(response.source_confidence),
            noise_floor_power=int(response.noise_floor_power),
            received_ts=time.time(),
        )

    def to_dict(self, *, include_signal: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "robot_time_stamp": self.robot_time_stamp,
            "group_id": self.group_id,
            "signal_power_len": len(self.signal_power),
            "direction_strengths_len": len(self.direction_strengths),
            "source_direction": self.source_direction,
            "source_confidence": self.source_confidence,
            "noise_floor_power": self.noise_floor_power,
            "received_ts": self.received_ts,
        }
        if include_signal:
            data["signal_power_b64"] = base64.b64encode(self.signal_power).decode("ascii")
        return data


class AudioCapture:
    def __init__(self, *, serial: str | None, subscriber_queue_size: int = 50) -> None:
        self.serial = serial
        self.subscriber_queue_size = subscriber_queue_size
        self.enabled = False
        self.connected = False
        self.frames_seen = 0
        self.signal_bytes_seen = 0
        self.last_frame: AudioFrame | None = None
        self.last_error: str | None = None
        self.started_ts: float | None = None
        self.repeated_frame_count = 0
        self.static_signal_detected = False

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._subscribers: set[asyncio.Queue[AudioFrame]] = set()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "connected": self.connected,
            "serial": self.serial,
            "service_path": SERVICE_PATH,
            "frames_seen": self.frames_seen,
            "signal_bytes_seen": self.signal_bytes_seen,
            "last_frame": self.last_frame.to_dict() if self.last_frame else None,
            "last_error": self.last_error,
            "started_ts": self.started_ts,
            "subscribers": len(self._subscribers),
            "repeated_frame_count": self.repeated_frame_count,
            "static_signal_detected": self.static_signal_detected,
        }

    async def start(self) -> dict[str, Any]:
        if self.enabled:
            return self.status()
        self._loop = asyncio.get_running_loop()
        self._stop_event = threading.Event()
        self.enabled = True
        self.connected = False
        self.last_error = None
        self.repeated_frame_count = 0
        self.static_signal_detected = False
        self.started_ts = time.time()
        self._thread = threading.Thread(target=self._thread_main, name="vector-audio-capture", daemon=True)
        self._thread.start()
        return self.status()

    async def stop(self) -> dict[str, Any]:
        self.enabled = False
        if self._stop_event:
            self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            await asyncio.to_thread(thread.join, 5)
        self._thread = None
        self._stop_event = None
        self.connected = False
        return self.status()

    async def frames(self, *, include_existing_latest: bool = False) -> AsyncIterator[AudioFrame]:
        queue: asyncio.Queue[AudioFrame] = asyncio.Queue(maxsize=self.subscriber_queue_size)
        if include_existing_latest and self.last_frame is not None:
            queue.put_nowait(self.last_frame)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    def _thread_main(self) -> None:
        try:
            import anki_vector

            robot_kwargs: dict[str, Any] = {"behavior_control_level": None, "default_logging": False}
            if self.serial:
                robot_kwargs["serial"] = self.serial

            with anki_vector.Robot(**robot_kwargs) as robot:
                self._call_soon(self._set_connected, True)
                future = robot.conn.run_coroutine(self._stream_audio_feed(robot.conn))
                future.result()
        except Exception as exc:
            self._call_soon(self._set_error, str(exc))
        finally:
            self._call_soon(self._set_connected, False)

    async def _stream_audio_feed(self, conn: Any) -> None:
        from anki_vector.messaging import messages_pb2

        call = conn._channel.unary_stream(
            SERVICE_PATH,
            request_serializer=messages_pb2.AudioFeedRequest.SerializeToString,
            response_deserializer=messages_pb2.AudioFeedResponse.FromString,
        )
        stream = call(messages_pb2.AudioFeedRequest())
        while not self._should_stop():
            try:
                response = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except StopAsyncIteration:
                break
            self._call_soon(self._emit_frame, AudioFrame.from_response(response))

    def _should_stop(self) -> bool:
        return bool((self._stop_event and self._stop_event.is_set()) or not self.enabled)

    def _call_soon(self, callback: Any, *args: Any) -> None:
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(callback, *args)

    def _set_connected(self, connected: bool) -> None:
        self.connected = connected

    def _set_error(self, error: str) -> None:
        self.last_error = error
        self.enabled = False

    def _emit_frame(self, frame: AudioFrame) -> None:
        if self.last_frame is not None and frame.signal_power == self.last_frame.signal_power:
            self.repeated_frame_count += 1
        else:
            self.repeated_frame_count = 0
        self.static_signal_detected = self.repeated_frame_count >= 10
        self.last_frame = frame
        self.frames_seen += 1
        self.signal_bytes_seen += len(frame.signal_power)
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(frame)
