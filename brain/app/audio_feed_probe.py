from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any


SERVICE_PATH = "/Anki.Vector.external_interface.ExternalInterface/AudioFeed"


async def _dump_audio_feed(conn: Any, *, output: Path, seconds: float, idle_timeout: float) -> dict[str, Any]:
    from anki_vector.messaging import messages_pb2

    output.parent.mkdir(parents=True, exist_ok=True)
    meta_path = output.with_suffix(output.suffix + ".json")
    call = conn._channel.unary_stream(
        SERVICE_PATH,
        request_serializer=messages_pb2.AudioFeedRequest.SerializeToString,
        response_deserializer=messages_pb2.AudioFeedResponse.FromString,
    )
    stream = call(messages_pb2.AudioFeedRequest())
    started = time.monotonic()
    deadline = started + seconds
    frames = 0
    signal_bytes = 0
    direction_bytes = 0
    signal_lengths: dict[int, int] = {}
    direction_lengths: dict[int, int] = {}
    first_response: dict[str, Any] | None = None
    last_response: dict[str, Any] | None = None

    with output.open("wb") as raw:
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                response = await asyncio.wait_for(stream.__anext__(), timeout=min(idle_timeout, remaining))
            except asyncio.TimeoutError:
                break
            except StopAsyncIteration:
                break

            signal = bytes(response.signal_power)
            direction = bytes(response.direction_strengths)
            raw.write(signal)
            frames += 1
            signal_bytes += len(signal)
            direction_bytes += len(direction)
            signal_lengths[len(signal)] = signal_lengths.get(len(signal), 0) + 1
            direction_lengths[len(direction)] = direction_lengths.get(len(direction), 0) + 1
            last_response = _response_summary(response)
            if first_response is None:
                first_response = {**last_response, "signal_power_hex_prefix": signal[:32].hex()}

    elapsed = time.monotonic() - started
    result = {
        "ok": frames > 0,
        "service_path": SERVICE_PATH,
        "output": str(output),
        "metadata": str(meta_path),
        "seconds_requested": seconds,
        "seconds_elapsed": round(elapsed, 3),
        "frames": frames,
        "signal_bytes": signal_bytes,
        "direction_bytes": direction_bytes,
        "signal_lengths": signal_lengths,
        "direction_lengths": direction_lengths,
        "first_response": first_response,
        "last_response": last_response,
        "notes": _notes(frames, signal_lengths),
    }
    meta_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _response_summary(response: Any) -> dict[str, Any]:
    return {
        "robot_time_stamp": int(response.robot_time_stamp),
        "group_id": int(response.group_id),
        "signal_power_len": len(response.signal_power),
        "direction_strengths_len": len(response.direction_strengths),
        "source_direction": int(response.source_direction),
        "source_confidence": int(response.source_confidence),
        "noise_floor_power": int(response.noise_floor_power),
    }


def _notes(frames: int, signal_lengths: dict[int, int]) -> str:
    if frames == 0:
        return "No AudioFeed frames arrived before the timeout."
    lengths = ", ".join(f"{length} bytes x{count}" for length, count in sorted(signal_lengths.items()))
    return f"Captured AudioFeed signal_power payloads: {lengths}. Raw file is concatenated signal_power bytes."


def dump_audio_feed_sync(
    *,
    serial: str | None,
    output: Path,
    seconds: float = 5.0,
    idle_timeout: float = 2.0,
) -> dict[str, Any]:
    import anki_vector

    robot_kwargs: dict[str, Any] = {"behavior_control_level": None, "default_logging": False}
    if serial:
        robot_kwargs["serial"] = serial

    with anki_vector.Robot(**robot_kwargs) as robot:
        future = robot.conn.run_coroutine(
            _dump_audio_feed(robot.conn, output=output, seconds=seconds, idle_timeout=idle_timeout)
        )
        return future.result(timeout=seconds + idle_timeout + 10)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump Vector AudioFeed signal_power bytes to a raw file.")
    parser.add_argument("--serial", default=None, help="Vector serial/ESN. Defaults to SDK config selection.")
    parser.add_argument("--seconds", type=float, default=5.0, help="Capture duration.")
    parser.add_argument("--idle-timeout", type=float, default=2.0, help="Stop if no frame arrives within this many seconds.")
    parser.add_argument("--output", default="captures/audio-feed.raw", help="Output raw file path.")
    args = parser.parse_args()

    result = dump_audio_feed_sync(
        serial=args.serial,
        output=Path(args.output).resolve(),
        seconds=args.seconds,
        idle_timeout=args.idle_timeout,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
