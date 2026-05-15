from __future__ import annotations

import asyncio
import math
import time
import wave
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np

from .audio_capture import AudioCapture, AudioFrame
from .schemas import ListenerConfig, ListenerStatus


RouteCallback = Callable[[str, dict[str, Any], ListenerConfig], Awaitable[dict[str, Any] | None]]


class Listener:
    def __init__(
        self,
        *,
        audio_capture: AudioCapture,
        capture_dir: Path,
        route_callback: RouteCallback | None = None,
    ) -> None:
        self.audio_capture = audio_capture
        self.capture_dir = capture_dir
        self.route_callback = route_callback
        self.config = ListenerConfig()
        self.last_error: str | None = None
        self.last_transcript: dict[str, Any] | None = None
        self.frames_seen = 0
        self.chunks_seen = 0
        self.utterances_seen = 0
        self.transcripts_seen = 0
        self.muted_until: float | None = None

        self._task: asyncio.Task | None = None
        self._pending: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=20)
        self._vad: Any | None = None
        self._vad_backend = "energy"
        self._stt_model: Any | None = None
        self._stt_model_name: str | None = None

        self._chunk_buffer = bytearray()
        self._pre_roll: deque[bytes] = deque()
        self._utterance = bytearray()
        self._speech_ms = 0
        self._silence_ms = 0
        self._in_speech = False

    def status(self) -> ListenerStatus:
        pending = self._pending.qsize()
        return ListenerStatus(
            enabled=self.config.enabled,
            auto_route=self.config.auto_route,
            execute=self.config.execute,
            dry_run=self.config.dry_run,
            audio_enabled=self.audio_capture.enabled,
            audio_connected=self.audio_capture.connected,
            audio_static_signal=bool(self.audio_capture.status().get("static_signal_detected")),
            vad_backend=self._vad_backend,
            stt_model=self.config.stt_model,
            stt_loaded=self._stt_model is not None,
            frames_seen=self.frames_seen,
            chunks_seen=self.chunks_seen,
            utterances_seen=self.utterances_seen,
            transcripts_seen=self.transcripts_seen,
            pending_transcripts=pending,
            last_transcript=self.last_transcript,
            last_error=self.last_error,
            muted_until=self.muted_until,
        )

    async def start(self, config: ListenerConfig) -> ListenerStatus:
        self.config = config.model_copy(update={"enabled": True})
        self.last_error = None
        self._reset_audio_state()
        self._setup_vad()
        if not self.audio_capture.enabled:
            await self.audio_capture.start()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return self.status()

    async def stop(self) -> ListenerStatus:
        self.config = self.config.model_copy(update={"enabled": False})
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        return self.status()

    async def pop_pending(self) -> dict[str, Any] | None:
        try:
            return self._pending.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def mute_for(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self.muted_until = max(self.muted_until or 0.0, time.time() + seconds)
        self._reset_audio_state()

    async def _run(self) -> None:
        try:
            async for frame in self.audio_capture.frames():
                if not self.config.enabled:
                    break
                self.frames_seen += 1
                if self.audio_capture.static_signal_detected:
                    self._reset_audio_state()
                    continue
                if self._is_muted():
                    continue
                await self._consume_frame(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = str(exc)

    async def _consume_frame(self, frame: AudioFrame) -> None:
        self._chunk_buffer.extend(frame.signal_power)
        chunk_size = self._chunk_size_bytes()
        while len(self._chunk_buffer) >= chunk_size:
            chunk = bytes(self._chunk_buffer[:chunk_size])
            del self._chunk_buffer[:chunk_size]
            self.chunks_seen += 1
            self._consume_chunk(chunk, self._is_speech(chunk))

    def _consume_chunk(self, chunk: bytes, is_speech: bool) -> None:
        if is_speech:
            if not self._in_speech:
                self._utterance = bytearray().join(self._pre_roll)
                self._speech_ms = 0
                self._silence_ms = 0
                self._in_speech = True
            self._utterance.extend(chunk)
            self._speech_ms += self.config.frame_ms
            self._silence_ms = 0
        elif self._in_speech:
            self._utterance.extend(chunk)
            self._silence_ms += self.config.frame_ms
        else:
            self._pre_roll.append(chunk)
            self._trim_pre_roll()

        if self._in_speech and self._silence_ms >= self.config.silence_ms:
            self._finish_utterance()
        elif self._in_speech and self._utterance_duration_ms() >= self.config.max_utterance_ms:
            self._finish_utterance()

    def _finish_utterance(self) -> None:
        pcm = bytes(self._utterance)
        speech_ms = self._speech_ms
        self._reset_audio_state()
        if speech_ms < self.config.min_speech_ms:
            return
        self.utterances_seen += 1
        asyncio.create_task(self._handle_utterance(pcm, speech_ms=speech_ms))

    async def _handle_utterance(self, pcm: bytes, *, speech_ms: int) -> None:
        utterance_id = self.utterances_seen
        raw_path = self.capture_dir / "listener-last.raw"
        wav_path = self.capture_dir / "listener-last.wav"
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(pcm)
        self._write_wav(wav_path, pcm)

        try:
            text, meta = await self._transcribe(pcm)
        except Exception as exc:
            self.last_error = f"STT failed: {exc}"
            return

        text = _clean_transcript(text)
        if not text:
            return

        payload: dict[str, Any] = {
            "id": utterance_id,
            "text": text,
            "speech_ms": speech_ms,
            "audio_ms": self._pcm_duration_ms(pcm),
            "raw_path": str(raw_path),
            "wav_path": str(wav_path),
            "stt": meta,
            "ts": time.time(),
            "routed": False,
        }
        self.transcripts_seen += 1
        self.last_transcript = payload
        self._put_pending(payload)

        if self.config.auto_route and self.route_callback is not None:
            self.mute_for(self.config.mute_after_route_seconds)
            try:
                route_result = await self.route_callback(text, payload, self.config)
                payload["routed"] = True
                payload["route_result"] = route_result
            except Exception as exc:
                payload["route_error"] = str(exc)
                self.last_error = f"route failed: {exc}"
            finally:
                self.mute_for(self.config.mute_after_route_seconds)
                self.last_transcript = payload

    async def _transcribe(self, pcm: bytes) -> tuple[str, dict[str, Any]]:
        model = await self._get_stt_model()
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        segments, info = await asyncio.to_thread(
            model.transcribe,
            audio,
            language=self.config.language,
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        segment_list = list(segments)
        text = " ".join(segment.text.strip() for segment in segment_list).strip()
        meta = {
            "model": self.config.stt_model,
            "language": getattr(info, "language", self.config.language),
            "language_probability": getattr(info, "language_probability", None),
            "duration": getattr(info, "duration", None),
            "segments": [
                {"start": segment.start, "end": segment.end, "text": segment.text.strip()}
                for segment in segment_list[:8]
            ],
        }
        return text, meta

    async def _get_stt_model(self) -> Any:
        if self._stt_model is not None and self._stt_model_name == self.config.stt_model:
            return self._stt_model

        def load_model() -> Any:
            from faster_whisper import WhisperModel

            return WhisperModel(self.config.stt_model, device="cpu", compute_type=self.config.compute_type)

        self._stt_model = await asyncio.to_thread(load_model)
        self._stt_model_name = self.config.stt_model
        return self._stt_model

    def _setup_vad(self) -> None:
        try:
            import webrtcvad

            self._vad = webrtcvad.Vad(self.config.vad_mode)
            self._vad_backend = "webrtcvad"
        except Exception as exc:
            self._vad = None
            self._vad_backend = f"energy fallback ({exc})"

    def _is_speech(self, chunk: bytes) -> bool:
        rms = _pcm_rms(chunk)
        if rms < self.config.min_rms:
            return False
        if self._vad is None:
            return True
        try:
            return bool(self._vad.is_speech(chunk, self.config.sample_rate))
        except Exception as exc:
            self.last_error = f"VAD failed; using energy fallback: {exc}"
            self._vad = None
            self._vad_backend = "energy fallback"
            return True

    def _chunk_size_bytes(self) -> int:
        return int(self.config.sample_rate * (self.config.frame_ms / 1000.0) * 2)

    def _trim_pre_roll(self) -> None:
        max_chunks = max(1, self.config.pre_roll_ms // self.config.frame_ms)
        while len(self._pre_roll) > max_chunks:
            self._pre_roll.popleft()

    def _utterance_duration_ms(self) -> int:
        return self._pcm_duration_ms(self._utterance)

    def _pcm_duration_ms(self, pcm: bytes | bytearray) -> int:
        if self.config.sample_rate <= 0:
            return 0
        return int((len(pcm) / 2) / self.config.sample_rate * 1000)

    def _is_muted(self) -> bool:
        return self.muted_until is not None and time.time() < self.muted_until

    def _reset_audio_state(self) -> None:
        self._chunk_buffer.clear()
        self._pre_roll.clear()
        self._utterance.clear()
        self._speech_ms = 0
        self._silence_ms = 0
        self._in_speech = False

    def _write_wav(self, path: Path, pcm: bytes) -> None:
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.config.sample_rate)
            wav.writeframes(pcm)

    def _put_pending(self, payload: dict[str, Any]) -> None:
        if self._pending.full():
            try:
                self._pending.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._pending.put_nowait(payload)


def _pcm_rms(pcm: bytes) -> int:
    if not pcm:
        return 0
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    if samples.size == 0:
        return 0
    return int(math.sqrt(float(np.mean(samples * samples))))


def _clean_transcript(text: str) -> str:
    text = " ".join(text.strip().split())
    if not text:
        return ""
    hallucination_fragments = (
        "thank you for watching",
        "thanks for watching",
        "subscribe",
        "music",
    )
    lowered = text.lower().strip(" .!")
    if lowered in hallucination_fragments:
        return ""
    return text[:500]
