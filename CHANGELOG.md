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
