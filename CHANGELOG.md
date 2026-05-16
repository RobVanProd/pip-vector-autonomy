# Changelog

## 2026-05-15 (Codex)

- Added operator scripts: `scripts/start-pip-autonomous.ps1` to bring up listener, voice bridge, vision, sentinel, and autonomy together; `scripts/start-sentinel.ps1` for direct sentinel control.
- Added `-ListenAfterSpeech` support to `scripts/start-voice-bridge.ps1`.
- Restored missing robot-state helpers in `brain/app/robot_io.py` so live state reads no longer fail on `_estimate_battery_percent`.
- Hardened vision capture by replacing `latest.jpg` atomically and filtering prompt-echo captions from moondream.
- Fixed the real SDK executor to connect with `behavior_control_level=None`, matching the working WireOS/vic-gateway path instead of timing out on behavior-control acquisition.
- Verified `vector-brain` on `127.0.0.1:8788`, WirePod voice bridge connected, listener/vision/sentinel/autonomy running, real `stop` execution working, and autonomy safely skipping while Pip is docked/asleep.
- Published a clean no-license public source snapshot to GitHub, excluding local runtime artifacts, logs, captures, downloads, venvs, and voice-reference files.
- Implemented Conversation Mode MVP: `conversation_session.py`, conversation config/status/reset API routes, WirePod first-turn routing, voice bridge delegation, listener pending polling while engaged, exit phrase handling, sparse cues, and silence timeout back to idle.
- Smoke-tested Conversation Mode with a dry-run WirePod transcript: session started, stripped the wake phrase, answered Rob's name, and returned to idle on timeout.
- Added external camera validation for Rob's Logi C615: `/external-camera/status`, `/external-camera/capture`, `/external-camera/latest.jpg`, and `/validation/pip-area`.
- Added `/validation/gemma-control`, a closed-loop embodied test that captures before/after external frames, asks Gemma for a safe visible action, executes through the Vector SDK, and validates physical change with robot telemetry plus image delta.
- Hardened planner calls with longer Ollama timeouts and `keep_alive` so Gemma survives cold loads and vision-model swaps.
- Tuned external camera validation to prefer `llava:7b` and use a prompt that detects Pip even when partially visible at the frame edge; added delayed DirectShow retries for camera-driver contention.
- Added `scripts/test-system.ps1` to exercise health, robot state, external camera validation, planner, and dry-run execution from one command.
- Fixed Conversation Mode reply windows so the local listener starts on wake, drains stale audio, mutes during Pip's own speech, then explicitly reopens after the echo guard instead of timing out early.
- Added optional external microphone capture through ffmpeg/DirectShow via `VECTOR_AUDIO_INPUT_DEVICE`; validated with `Microphone (Logi C615 HD WebCam)` after Vector's audio feed produced repeated/static frames.
- Added `/validation/control-suite` with expected-direction checks, plus `/map/status` and `/map/observe` for durable pose + camera observations.
- Added planner latency metrics from Ollama, `/llm/warmup`, `/diagnostics/latency-sample`, and `scripts/run-extended-harness.ps1` / `scripts/analyze-extended-harness.ps1`.
- Reduced planner output budget and memory context for faster warm responses; separated routine map vision (`VECTOR_MAP_VISION_MODEL`) from validation vision and rewarm Gemma after vision-heavy calls to avoid model-swap latency spikes.
