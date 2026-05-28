# pip-vector-autonomy

`pip-vector-autonomy` contains a 25-module FastAPI brain service for an Anki Vector robot, with a deterministic safety filter between LLM action plans and robot SDK execution.

## What It Is

This repo is an embodied-AI control harness for a dev-unlocked Anki Vector robot. The design is explicit: a local model proposes JSON actions, then a non-LLM safety layer validates, clamps, blocks, and appends stop actions before anything reaches the robot executor.

The system separates personality, memory, emotion state, goals, telemetry, planning, safety, execution, voice routing, vision, and dashboard code. The architecture docs describe an autonomy loop, a faster sentinel loop for reactive events, and a WirePod/voice bridge path.

## Current Status

The implementation includes:

- `brain/app/safety.py` for deterministic action filtering
- `brain/app/planner.py` for prompt construction, plan parsing, cleanup, fallback speech, and safety handoff
- `brain/app/robot_io.py` and `executors.py` for SDK-facing state and action execution
- `memory.json` for persisted robot state
- `compose.yaml` and a `brain/Dockerfile` for service runtime

No automated tests are present; `python -m pytest -q` reported `no tests ran` on 2026-05-28.

## Tech Stack

- Python
- FastAPI
- Pydantic
- Docker Compose
- Anki Vector SDK/gRPC concepts
- Local LLM/Ollama integration described in the docs

## Limitations

This is hardware-dependent. Claims about robot behavior require a dev-unlocked Vector and live SDK validation. The safety layer is implemented in code, but the complete physical loop should be tested with the robot on a charger and conservative motion settings.
