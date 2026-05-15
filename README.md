# Vector Local LLM Project

Goal: unlock Rob's Anki Vector and run a local "brain" loop where a small local Gemma model can drive behavior through a safe control layer.

## Current Plan

1. Unlock / prepare Vector
   - Keep Vector on charger throughout unlock.
   - Put Vector in recovery mode: on charger, hold back button ~15s until rear lights turn dark blue / recovery screen appears.
   - Use Chromium browser with Bluetooth support.
   - Rob-provided unlock path: https://unlock-prod.froggitti.net/ and setup flow via https://websetup.froggitti.net/.
   - Select Utility stack, pair, connect Wi-Fi, choose `Unlock-Prod.ota`, wait for reboot (~7 min).

2. Install / run local server
   - Prefer wire-pod as local voice/server replacement: https://github.com/kercre123/wire-pod
   - wire-pod supports Vector 1.0/2.0 and can authenticate the robot locally.
   - It provides local voice commands, web app replacement, SDK config generation, custom commands, and LLM/intent graph options.
   - Docker is preferred where feasible, consistent with our security posture.

3. Local LLM control architecture
   - Vector <-> wire-pod / SDK control layer <-> local intent router <-> local Gemma model.
   - The LLM should not directly execute arbitrary robot actions.
   - Use a strict command schema: say(text), drive(mm/s, seconds), turn(deg), lift(up/down), look/head angle, play animation, take photo/get state, stop.
   - Add safety limits: speed caps, time caps, cliff/proximity checks if exposed, no continuous motion without heartbeat, emergency stop.
   - Keep a deterministic router for robot-critical commands; let Gemma choose high-level actions only.

## Notes from initial research

- Froggitti unlock page says keep Vector on charger, enter recovery, use websetup.froggitti.net, select Utility stack, pair, connect Wi-Fi, and apply Unlock-Prod.ota.
- wire-pod is a free local server for Vector based on DDL/open-sourced chipper/Escape Pod work.
- wire-pod can work without DDL servers, supports local auth, custom commands, and can write SDK config for Python SDK use.
- The original Anki Vector Python SDK docs site appears unavailable, but the GitHub repo remains: https://github.com/anki/vector-python-sdk

## First experiments once Vector arrives

- Confirm model/version and whether it is Vector 1.0 or 2.0.
- Complete unlock + wire-pod setup.
- Verify basic local voice command works.
- Verify SDK connection from this machine.
- Build a tiny `vector-brain` prototype:
  - local HTTP/WebSocket service
  - Gemma prompt returns JSON action plans only
  - validator clamps/denies unsafe actions
  - executor sends actions to Vector SDK/wire-pod
  - log every action and sensor state

## Security / Safety

- Treat unlock/setup downloads as external/untrusted until verified.
- Prefer Docker/containerized services where practical.
- Do not expose Vector control API to the public internet.
- Bind local control services to LAN/local only.
- No cloud credentials or GitHub/npm tokens mounted into containers.
- Include physical emergency stop behavior: back button/charger/manual pickup.
