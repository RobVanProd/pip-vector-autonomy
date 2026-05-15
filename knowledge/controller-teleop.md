# Game Controller / Teleoperation Plan

Rob wants PS5 or Xbox controller support. This should be a specific mode, not the default autonomous mode.

## Purpose

1. Fun manual control mode.
2. Demonstration data collection for future policies.
3. Safety fallback / testing tool for every movement Vector can do.

## Proposed modes

### Mode A — Manual Teleop

Controller maps directly to bounded robot controls:
- Left stick Y: forward/backward tread speed.
- Left stick X or right stick X: turn / differential drive.
- Triggers: lift up/down.
- Right stick Y: head up/down.
- A/X button: say canned phrase or play happy animation.
- B/Circle: stop all motors.
- Menu/Options: toggle deadman/manual mode.

Safety:
- Deadman button required for movement.
- Command heartbeat every 100ms; if no controller event for 250-500ms, stop motors.
- Speed caps and ramping to avoid sudden jolts.
- Always log input + resulting command + robot state.

### Mode B — Assisted Teleop

Controller selects intent; Gemma/personality fills style:
- Button asks Vector to greet, celebrate, inspect, react.
- Stick still controls movement.
- LLM can choose speech/animation but not override deadman/stop.

### Mode C — Demonstration Collection

Record episodes:
- timestamp
- controller axes/buttons
- validated robot command
- robot state/sensors
- camera frame reference if available
- human label / task label

This becomes training/evaluation data for imitation learning or policy prompting.

## Implementation options

Python options:
- `pygame` joystick module: widely used, good enough for PS/Xbox controllers.
- `inputs` package: raw gamepad events, simple for Linux/Windows but device mappings can vary.
- Web Gamepad API: browser page reads controller, sends WebSocket commands to local service; nice UI and avoids Python HID quirks.

Recommended first implementation: browser Gamepad API + local WebSocket endpoint. It is easy to visualize axes/buttons, works with PS5/Xbox in Chrome/Edge, and keeps robot API server-side.

## Data policy idea

Every manual session saves JSONL under `vector/runs/<timestamp>/teleop.jsonl`.
Later policy experiments can replay or summarize episodes.

## Not first priority

Controller mode is less important than:
1. Unlocking Vector.
2. wire-pod auth.
3. basic SDK connection.
4. safe LLM mock-to-real executor.

But the scaffolding should be designed so teleop plugs into the same validator/executor pipeline.
