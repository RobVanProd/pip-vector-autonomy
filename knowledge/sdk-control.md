# Vector SDK / Control Surface Notes

## SDK modules of interest

From Anki Vector Python SDK repo:
- `behavior.py` — higher-level behaviors.
- `motors.py` — direct wheel/head/lift motor control.
- `animation.py` — play animations.
- `camera.py`, `photos.py`, `vision.py` — perception/camera.
- `faces.py`, `proximity.py`, `touch.py`, `status.py` — state/sensors.
- `world.py`, `objects.py`, `nav_map.py` — cube/world/navigation.

## Known basic SDK pattern

```python
import anki_vector

with anki_vector.Robot(serial) as robot:
    robot.behavior.say_text("Hello World")
```

## Physical controls we likely want

High-level safe commands:
- `say_text(text)`
- `set_eye_color(hue, saturation)`
- `drive_straight(distance, speed)`
- `turn_in_place(angle)`
- `set_head_angle(angle)`
- `set_lift_height(height)`
- `play_animation(name)`
- `drive_on_charger()` / `drive_off_charger()`
- `look_around_in_place()`
- `find_faces()`

Low-level teleop:
- `motors.set_wheel_motors(left_mmps, right_mmps, ...)`
- `motors.set_head_motor(speed_rad_per_sec)`
- `motors.set_lift_motor(speed_rad_per_sec)`
- `motors.stop_all_motors()`

## Safety clamps

Initial caps:
- wheel speed: ±80 mm/s for LLM mode, maybe ±120 mm/s for manual controller mode.
- continuous drive command: max 2 seconds without renewed command.
- turn: ±90 degrees per command.
- head: -20 to +40 degrees.
- lift: low/medium/high abstraction before raw motor speeds.
- emergency stop endpoint always available.

## Executor shape

The `vector-brain` service currently has mock endpoints:
- `GET /health`
- `POST /plan`
- `POST /execute`

Next step after robot setup: replace mock `/execute` with a real executor using the wire-pod-compatible Python SDK, while preserving validation and logs.
