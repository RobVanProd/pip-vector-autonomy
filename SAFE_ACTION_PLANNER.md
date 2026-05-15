# Safe Action Planner and Embodiment Loop

This layer is now the direct local brain path: Vector's Python SDK connects to the robot, Gemma plans safe actions, and the host executes them without WirePod running.

WirePod can stay off for this setup. The one important limitation is voice input: the installed SDK exposes wake-word and native `user_intent` events, but it does not expose a raw microphone stream or open-ended transcript. That means the direct bridge can route recognized native intents to Gemma, while true arbitrary speech-to-text from Vector's mic needs a lower-level audio/STT bridge later.

## Current status

- Docker Compose still runs in `mock` mode for isolated planner tests.
- Host direct SDK service runs on `http://127.0.0.1:8788`.
- Gemma planner is available at `POST /plan`.
- Safety-filtered execution is available at `POST /execute`.
- Combined dry-run flow is available at `POST /plan_execute`.
- Real SDK execution is enabled in host mode with `VECTOR_EXECUTION_MODE=vector-sdk`.
- Live robot state is available at `GET /robot/state`.
- Camera capture + vision summary is available at `POST /robot/look`.
- Autonomy can refresh camera context automatically while Vector is awake/off the charger. The current default vision chain is `moondream:latest,llava:7b`, using Moondream first and LLaVA as fallback.
- Raw robot microphone feed is proven through the unwrapped gRPC `AudioFeed` method. The installed SDK has `AudioFeedRequest`/`AudioFeedResponse` in `messages_pb2`, but its generated `ExternalInterfaceStub` does not expose `AudioFeed`, so the harness calls the raw service path directly.
- Audio capture API:
  - `POST /audio/start`
  - `POST /audio/stop`
  - `GET /audio/status`
  - WebSocket `/audio`
- Listener/STT API:
  - `POST /listener/start`
  - `POST /listener/stop`
  - `GET /listener/status`
  - `GET /listener/pending`
- Passive native voice bridge endpoints:
  - `POST /voice/start`
  - `POST /voice/stop`
  - `GET /voice/status`
- Autonomy endpoints exist for opt-in idle embodiment:
  - `POST /autonomy/tick`
  - `POST /autonomy/start`
  - `POST /autonomy/stop`
  - `GET /autonomy/status`
- Autonomy respects sleep/charger state before planning. If Vector is charging, on the charger, or in calm power mode, it logs `autonomy_skip` and sends no Gemma action.

## Test without the robot

```powershell
cd "C:\Users\Rob\Documents\New project 5\vector"
docker compose up --build -d
.\scripts\test-brain.ps1
```

Expected result: `/plan` returns Gemma actions, and `/execute` returns `mode=mock` with a final `stop` action. If `robot_state.on_charger=true`, drive/turn actions are denied.

## Host-side dry run

Use this when we want to test the real SDK import/config path without moving Vector:

```powershell
cd "C:\Users\Rob\Documents\New project 5\vector"
.\scripts\setup-host-sdk.ps1
.\scripts\run-brain-host.ps1 -Mode vector-sdk-dry-run -Serial 0dd1fb2d
```

Then, in another PowerShell:

```powershell
.\scripts\test-brain.ps1 -BaseUrl http://127.0.0.1:8788
```

## First real robot test

Only after dry-runs look good:

```powershell
.\scripts\run-brain-host.ps1 -Mode vector-sdk -Serial 0dd1fb2d
```

Start with expression-only actions:

```powershell
$body = @{
  actions = @(
    @{ type = "say"; text = "Local action planner online." },
    @{ type = "head"; angle_deg = 10 },
    @{ type = "stop" }
  )
  robot_state = @{ on_charger = $true }
  dry_run = $false
} | ConvertTo-Json -Depth 10

Invoke-RestMethod http://127.0.0.1:8788/execute -Method Post -ContentType "application/json" -Body $body
```

Keep Vector on the charger for the first test. The safety policy blocks drive/turn while `on_charger=true`.

## Embodiment loop

This is the "make Vector feel alive" layer. It periodically asks Gemma for a tiny micro-behavior and routes it through the same safety filter and executor.

Default loop rules:
- opt-in only
- dry-run unless `-Real` is passed
- no drive/turn unless `-AllowMotion` is passed
- sleep/charger aware by default; use `-IgnoreSleep` only for deliberate experiments
- no fake listen-after-speech by default
- vision refreshes every 2 autonomy ticks by default; set `-VisionIntervalTicks` to tune it
- interval is clamped to 15-300 seconds
- every tick ends with `stop`

Dry-run one tick:

```powershell
.\scripts\test-autonomy.ps1 -BaseUrl http://127.0.0.1:8788
```

Start a dry-run loop:

```powershell
.\scripts\start-autonomy.ps1 -BaseUrl http://127.0.0.1:8788 -IntervalSeconds 45
```

Start a real expression-only loop:

```powershell
.\scripts\start-autonomy.ps1 -BaseUrl http://127.0.0.1:8788 -IntervalSeconds 45 -Real
```

Start the current real embodied loop with motion and vision:

```powershell
.\scripts\start-autonomy.ps1 -BaseUrl http://127.0.0.1:8788 -IntervalSeconds 30 -VisionIntervalTicks 2 -SpeakProbability 0.65 -AllowMotion -Real
```

Enable the old experimental app-intent reply behavior:

```powershell
.\scripts\start-autonomy.ps1 -BaseUrl http://127.0.0.1:8788 -IntervalSeconds 45 -Real -ListenAfterSpeech
```

Stop the loop:

```powershell
.\scripts\stop-autonomy.ps1 -BaseUrl http://127.0.0.1:8788
```

## Current live notes

- Real SDK host brain runs on `http://127.0.0.1:8788`.
- Dashboard runs at `http://127.0.0.1:8788/dashboard`.
- Real autonomy loop has been tested expression-only with `allow_motion=false`, and later with `allow_motion=true` in the safe area.
- Live state reports Vector sleeping/on charger/charging/calm-power correctly and autonomy skips in that state.
- Vision capture was tested through `/robot/look`. Moondream needs a short prompt and may fall back to LLaVA; both outputs are capped before being inserted into Gemma's planner context.
- As of the latest live run, Vector is charging/asleep, voice bridge is connected, and autonomy is enabled but cleanly logging `autonomy_skip` until he wakes/off-dock.
- Passive native voice bridge is connected with `use_behavior_control=false`, so it waits for `wake_word` and `user_intent` events without taking movement control.
- Direct animation execution bypasses SDK animation-list lazy loading by calling `PlayAnimation` with known safe animation names.
- The robot can already speak through SDK `SayText`; the missing piece is arbitrary STT from Vector's microphone, not TTS.
- Stage 1 audio proof captured `AudioFeed` while Vector was asleep/on charger: 35 frames in 5 seconds, each with 3200 bytes of `signal_power`, written to `captures/audio-feed-stage1.raw` with metadata in `captures/audio-feed-stage1.raw.json`.
- Stage 2 audio capture component lives in `brain/app/audio_capture.py`. It keeps a background raw gRPC stream open, broadcasts frames to subscribers, and exposes WebSocket debug frames. It is not wired to STT yet.
- Stage 3 listener component lives in `brain/app/listener.py`. It uses WebRTC VAD and `faster-whisper` `tiny.en`, saves `captures/listener-last.raw`/`.wav`, and can auto-route transcripts into Gemma chat when explicitly started with `auto_route=true`.
- Important blocker: the current robot `AudioFeed.signal_power` is not behaving like live microphone PCM. Captures while Vector spoke repeated the same 3200-byte frame and faster-whisper only hallucinated "You". The public proto/TRM confirms audio processing modes exist in messages, but the command to set them was not implemented/was removed, and this SDK/service exposes no `AudioSendMode` RPC. Treat listener routing as experimental until the feed produces changing mic samples.
- The SDK may print `StopAsyncIteration` messages while closing behavior streams; check `/autonomy/status` for the actual loop health.

## Audio feed probe

Run the standalone Stage 1 probe:

```powershell
cd "C:\Users\Rob\Documents\New project 5"
.\vector\brain\.venv\Scripts\python.exe .\vector\brain\app\audio_feed_probe.py --serial 0dd1fb2d --seconds 5 --output captures\audio-feed-stage1.raw
```

Try playback guesses:

```powershell
ffplay -f s16le -ar 16000 -ac 1 captures\audio-feed-stage1.raw
ffplay -f s16le -ar 15625 -ac 1 captures\audio-feed-stage1.raw
```

The exact sample rate still needs confirmation by listening and/or STT quality. The payload shape is stable so far: `signal_power` is 3200 bytes per frame, `direction_strengths` is empty, and `source_direction=12` means invalid/no confident direction.

## Listener / STT

Install dependencies:

```powershell
cd "C:\Users\Rob\Documents\New project 5\vector"
.\brain\.venv\Scripts\python.exe -m pip install -r .\brain\requirements-host.txt
```

Start listen/transcribe-only mode:

```powershell
.\scripts\start-listener.ps1 -BaseUrl http://127.0.0.1:8788
```

Start experimental Gemma routing without real execution:

```powershell
.\scripts\start-listener.ps1 -BaseUrl http://127.0.0.1:8788 -AutoRoute -Execute
```

Start experimental Gemma routing with real robot execution:

```powershell
.\scripts\start-listener.ps1 -BaseUrl http://127.0.0.1:8788 -AutoRoute -Execute -Real
```

Do not use real listener routing yet unless `/audio/status` shows `static_signal_detected=false` after many frames and `/listener/status` shows actual transcripts from human speech. The current direct feed appears static/invalid on this robot firmware path.

## Dashboard

Open:

```text
http://127.0.0.1:8788/dashboard
```

It shows recent `plan`, `execute`, `autonomy_plan`, `autonomy_skip`, `voice_wake`, `voice_intent`, `voice_gemma_execute`, `vision`, and `error` events. It also shows live robot state and latest camera observation.

The dashboard also has a text box:
- `Plan` asks Gemma for actions without executing them.
- `Speak` asks Gemma and executes the safe action plan on Vector.

## Memory

Direct brain memory is stored in `memory.json` next to the vector project service. It includes durable facts and recent turns. The planner and autonomy loop both receive this context, so Vector can remember basics like Rob's name.

Current direct setup does not require WirePod. Wake-word/native-intent events are handled by the SDK voice bridge. Open-ended speech without WirePod still needs a separate robot-mic STT bridge or a lower-level implementation of the SDK `AudioFeed` path.
