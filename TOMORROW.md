# Vector Tomorrow Quickstart

## Before Vector arrives

Prepared:
- Docker is installed.
- Ollama is installed and serving locally.
- `gemma4:e4b` is already present in Ollama.
- `vector-brain` Docker service scaffold exists.
- WirePod Windows installer `v1.2.18` is downloaded and SHA-256 verified under `vector/downloads/`.

Verified 2026-05-13:
- `docker compose up --build -d` starts `vector-brain`.
- `GET /health` reports `model=gemma4:e4b` and `execution_mode=mock`.
- `POST /plan` reaches Ollama and returns safe JSON actions.
- `POST /execute` works in mock mode.
- WirePod UI is reachable at `http://192.168.1.213:8080/`.
- WirePod local state marks Vector `0dd1fb2d` / `Vector-B3Z8` as `activated=true`.
- Vector is currently discoverable as `Vector-B3Z8.local` / `192.168.1.228`, not `192.168.1.220`.
- Vector answers on HTTPS port `443`; SSH port `22` is closed, which is expected for the production auth path.
- WirePod Knowledge Graph is configured to use local Ollama through `custom` provider:
  - endpoint: `http://127.0.0.1:11434/v1`
  - model: `gemma4:e4b`
  - robot name: `Pip`
  - intent graph: enabled
  - saved chat: enabled
  - limited WirePod LLM animation/camera commands: enabled
- Safe action planner layer is scaffolded in `vector-brain` v0.2:
  - `POST /plan` asks Gemma for JSON actions.
  - `POST /execute` safety-filters actions and runs mock or SDK executor.
  - `POST /plan_execute` combines both for dry-runs.
  - Docker stays in mock mode on `127.0.0.1:8787`.
  - Host SDK runner defaults to `127.0.0.1:8788`.
  - Host venv has the wire-pod-compatible `anki_vector` SDK installed and import-tested.
  - Opt-in embodiment loop exists via `/autonomy/*`; it can run Ralph-Wiggum-style idle micro-behaviors through the same safety filter.

## Start local brain mock

```powershell
cd "C:\Users\Rob\Documents\New project 5\vector"
docker compose up --build
```

Test:

```powershell
Invoke-RestMethod http://127.0.0.1:8787/health
Invoke-RestMethod http://127.0.0.1:8787/plan -Method Post -ContentType 'application/json' -Body '{"user_text":"say hi and do a tiny happy move","robot_state":{"on_charger":true}}'
```

## Tomorrow physical setup

1. Keep Vector on charger.
2. Put Vector in recovery mode.
3. Use Chromium + Bluetooth at https://websetup.froggitti.net/
4. Utility stack -> pair -> Wi-Fi -> `Unlock-Prod.ota`.
5. Install/run WirePod.
6. Authenticate robot with WirePod.
7. Verify SDK config / basic command.
8. Replace mock executor with real Vector executor.

## Authentication note

For the Froggitti `Unlock-Prod.ota` / production Vector path, do not use WirePod's OSKR/dev bot setup flow. That flow asks for SSH material. Production authentication should be done from WirePod `Bot Setup` using the production/authenticate path over BLE. Current WirePod state already shows `activated=true` for `Vector-B3Z8`.

## Gemma voice test

Say:

```text
Hey Vector, I have a question.
```

or:

```text
Hey Vector, let's talk.
```

Then ask a short question. WirePod should route the knowledge/conversation request to local Ollama `gemma4:e4b`.

## Safe planner test

```powershell
cd "C:\Users\Rob\Documents\New project 5\vector"
.\scripts\test-brain.ps1
```

For host SDK dry-run:

```powershell
.\scripts\run-brain-host.ps1 -Mode vector-sdk-dry-run -Serial 0dd1fb2d
```

In another PowerShell:

```powershell
.\scripts\test-brain.ps1 -BaseUrl http://127.0.0.1:8788
```

## WirePod download helper

```powershell
cd "C:\Users\Rob\Documents\New project 5\vector"
.\scripts\fetch-wirepod.ps1 -Asset installer
```

The helper reads `downloads/wirepod-latest.json`, downloads the selected asset if needed, and verifies SHA-256 before reporting the local path.

## Safety rule

Gemma never directly controls raw robot APIs. It returns a JSON plan; validator clamps actions; executor performs only allowed commands.
