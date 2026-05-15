# Vector Brain — Architecture Reference

**Project:** Pip, a local AI personality running inside an Anki Vector robot  
**Owner:** Rob  
**AI Collaborators:** Claude (Anthropic), Codex (OpenAI) — both work on this codebase  
**Last updated:** 2026-05-14

---

## North Star

Make Vector feel like the free-roaming autonomous droids at Star Wars: Galaxy's Edge —  
a continuous inner life that drives outward behavior, not a command-response machine.

The robot has a **mood**, a **current mission**, and **attention** that can be interrupted  
by things it notices. Gemma 4 is the intelligence. The harness gives it the grounded  
context it needs to produce authentic, in-character, non-repetitive behavior.

**Design principle: LLM proposes. Safety layer disposes.**  
Gemma never touches raw robot APIs. It returns a JSON action plan. The validator clamps  
it. The executor runs only what passed. This is non-negotiable.

---

## Dev-Unlocked LLM-Driven Control Architecture

This is not a cloud replacement. Most WirePod deployments simply restore Vector's
original cloud behavior — Anki's server is replaced by a local one that responds the
same way. This harness uses dev firmware to take over the control plane entirely.

The distinction matters. Here is what changes when Gemma 4 is the decision authority:

---

### 1. Telemetry as Prompt Input

`robot_io.py` reads live telemetry on every tick:

- Battery voltage → estimated percentage (via calibrated voltage curve)
- Pose (x, y, z, angle) from robot odometry
- Head angle, lift height
- Charger contact, charging state, calm power mode
- Picked-up / being-held status
- Cliff detected, obstacle proximity
- Face detected, cube detected (SDK `world` object)

This data is serialized as JSON and injected directly into the Gemma CHARACTER block.
Gemma reasons about live hardware state, not canned context.

---

### 2. Decision Authority — Gemma Drives Behavior

Standard Vector idle behavior is a hard-coded native behavior tree managed by Anki's
firmware. The robot picks animations, sounds, and movements from a fixed palette based
on internal state variables it controls entirely.

In this harness:

- The **Autonomy loop** (45s ticks) sends the live telemetry + full personality context to Gemma and asks it to select micro-behaviors
- The **Sentinel loop** (2s reflex) detects state edges (face appeared, picked up, battery crossed threshold) and fires immediate reactive Gemma calls
- Gemma selects specific action types: `say`, `animation`, `head`, `lift`, `drive`, `turn`, `behavior`
- Anki's native idle animations only play if Gemma explicitly requests them by name

The robot's moment-to-moment behavior is now an LLM output, not a firmware output.

---

### 3. Direct Execution via gRPC

Because the robot is running dev-unlocked firmware, `executors.py` can send raw gRPC
commands that bypass the default behavior tree entirely:

| Gemma output | gRPC call |
|---|---|
| `say("hello Rob")` | `SayTextRequest` → TTS engine |
| `animation: "happy"` | `PlayAnimationRequest` → native anim |
| `head: 14°` | `SetHeadAngleRequest` |
| `lift: "high"` | `SetLiftHeightRequest` |
| `drive: 50mmps, 800ms` | `DriveStraightRequest` |
| `turn: 30°` | `TurnInPlaceRequest` |
| `behavior: "look_around"` | `StartBehaviorRequest` → native behavior |
| `behavior: "go_home"` | `StartBehaviorRequest` → dock return |

On a stock (non-dev) robot, this level of direct command is not available. The dev
firmware is what makes programmatic control at this granularity possible.

---

### 4. State Modulation — Persistent Inner Life

Two engines run in-process and persist across Docker restarts via `memory.json`:

**EmotionEngine** (`emotion.py`)
- 7 mood states: `CURIOUS | CONTENT | ALERT | PLAYFUL | CAUTIOUS | TIRED | EXCITED`
- Transitions are deterministic rule evaluations (no LLM) — fast and predictable
- Mood biases `speak_probability` additively: PLAYFUL +25%, TIRED -20%
- Mood sets preferred animations: EXCITED prefers `veryHappy`, `celebrate`, `love`
- Mood description is injected into every Gemma prompt as grounding context

**GoalEngine** (`goals.py`)
- 7 goals: `EXPLORING | WATCHING | INVESTIGATING | SOCIALIZING | CELEBRATING | SEEKING_CHARGER | RESTING`
- Each goal has a tick budget and priority; higher-priority goals interrupt lower ones
- Goal description and tick progress are injected into every Gemma prompt
- Consecutive ticks share a narrative thread: "Pip is investigating. Tick 2 of 3."

Together, these engines mean that Pip's behavior emerges from a combination of:
- Live hardware state (telemetry)
- Persistent internal state (mood, mission)
- Gemma's in-context reasoning

This is what produces behavior that feels continuous rather than episodic.

---

### 5. Safety Layer — Hardware-Level Gatekeeper

Gemma's JSON output is never sent to the robot directly. It passes through
`safety_filter()` first, which is deterministic and cannot be bypassed by any
LLM output:

**Blocked unconditionally when state requires it:**
- `drive` / `turn` while charging, on charger, picked up, cliff detected
- `drive` / `turn` while low battery or obstacle close
- Any motion behavior (`look_around`, `roll_visible_cube`, etc.) under the same conditions

**Always clamped:**
- Drive speed: ±80 mmps max
- Drive duration: 2000ms max
- Turn angle: ±90° max
- Say text length: 180 chars max
- Animation names: must match known valid pattern

**Always appended:**
- A `stop` action is always added as the final action, regardless of Gemma's output

The result: Gemma has full creative authority over Pip's personality and behavior,
but zero ability to damage the hardware. The safety layer is the separation of
"what Pip wants to do" from "what Pip is allowed to do."

---

```
vector/
├── ARCHITECTURE.md          ← you are here
├── COLLABORATION.md         ← AI agent working guide (READ THIS FIRST)
├── KNOWLEDGE_BASE.md        ← hardware/SDK reference notes
├── README.md                ← setup overview
├── SAFE_ACTION_PLANNER.md   ← original safety design doc
├── TOMORROW.md              ← physical setup quickstart
├── compose.yaml             ← Docker Compose for vector-brain service
├── memory.json              ← persistent robot memory (auto-managed)
│
├── brain/                   ← Python FastAPI service ("vector-brain")
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py          ← FastAPI app, all HTTP routes, startup
│       ├── personality.py   ← Pip character block, TTS cleanup, text utils
│       ├── emotion.py       ← Emotional state machine (mood persistence)
│       ├── goals.py         ← Active goal / mission system
│       ├── sentinel.py      ← Event-driven interrupt loop
│       ├── autonomy.py      ← Ralph-Wiggins idle loop (scheduled ticks)
│       ├── planner.py       ← Gemma prompt builder + JSON plan parser
│       ├── executors.py     ← SDK executor + mock executor
│       ├── safety.py        ← Deterministic action filter (the hard wall)
│       ├── schemas.py       ← Pydantic models for all API types
│       ├── memory.py        ← memory.json read/write, fact extraction
│       ├── robot_io.py      ← SDK robot state reads, camera capture
│       ├── vision_loop.py   ← Scheduled camera + vision description loop
│       ├── voice_bridge.py  ← Wire-pod intent → Gemma routing
│       ├── listener.py      ← Microphone VAD + Whisper STT
│       ├── audio_capture.py ← Raw audio from Vector's mic
│       ├── audio_feed_probe.py
│       ├── openai_proxy.py  ← OpenAI-compatible proxy to local Ollama
│       ├── events.py        ← In-memory event log (last 200 events)
│       └── dashboard.py     ← HTML dashboard served at /
│
├── personality/             ← Character design docs (not loaded by code)
│   ├── pip.md               ← Full character spec for Pip
│   ├── pip-system-prompt.md ← Earlier system prompt draft
│   ├── tools-design.md      ← Tool/web access design
│   └── voice-tts.md         ← TTS voice design notes
│
├── knowledge/               ← SDK and hardware reference
│   ├── sdk-control.md
│   ├── wirepod.md
│   ├── controller-teleop.md
│   └── policy-training.md
│
└── voice-lab/               ← TTS voice experiments (standalone)
```

---

## Runtime Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    vector-brain (FastAPI)                │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ AutonomyLoop │  │   Sentinel   │  │  VoiceBridge │  │
│  │ (45s ticks)  │  │  (2s poll)   │  │ (wire-pod)   │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                  │           │
│         └────────┬────────┘                  │           │
│                  ▼                            ▼           │
│           ┌─────────────┐            ┌──────────────┐    │
│           │   planner   │            │   planner    │    │
│           │ (idle tick) │            │ (intent/voice)│   │
│           └──────┬──────┘            └──────┬───────┘    │
│                  │                          │             │
│                  ▼                          ▼             │
│           ┌────────────────────────────────────────┐      │
│           │           safety_filter()              │      │
│           │  (deterministic — Gemma cannot bypass) │      │
│           └────────────────────┬───────────────────┘      │
│                                ▼                          │
│           ┌────────────────────────────────────────┐      │
│           │         Executor (mock | SDK)          │      │
│           │  → say / drive / turn / head / anim    │      │
│           └────────────────────────────────────────┘      │
│                                                           │
│  ┌───────────┐  ┌───────────┐  ┌──────────┐              │
│  │ emotion   │  │  goals    │  │  memory  │  ← shared    │
│  │ (state)   │  │ (mission) │  │ (facts)  │    state     │
│  └───────────┘  └───────────┘  └──────────┘              │
└─────────────────────────────────────────────────────────┘
         │                              │
         ▼                              ▼
   Ollama (local)                Anki Vector SDK
   gemma4:e4b                    (gRPC over Wi-Fi)
```

---

## Module Responsibilities

### `personality.py`
Single source of truth for Pip's character. Provides:
- `CHARACTER` — the full identity block injected into every Gemma prompt
- `time_context()` — current local time string for prompt injection
- `clean_say_text(text)` — strips `[chirp]` / `[happy trill]` stage cues before TTS
- `is_forbidden_speech(text)` — detects filler/policy-narration speech
- `summarize_actions(actions)` — compact action summary for previous-tick context
- `FORBIDDEN_SUBSTRINGS` — patterns that indicate low-quality Gemma output

**Do not put robot SDK calls here. Pure text utilities only.**

### `emotion.py`
Persistent emotional state machine. Pip has one active mood at all times.

States: `CURIOUS | CONTENT | ALERT | PLAYFUL | CAUTIOUS | TIRED | EXCITED`

- Mood persists across restarts (stored in `memory.json["emotion"]`)
- Transitions are **rule-based and deterministic** (no LLM involvement)
- Mood is injected into every Gemma prompt as grounding context
- Mood biases `speak_probability` and animation preferences
- Key API:
  - `EmotionEngine.state` — current MoodState
  - `EmotionEngine.update(robot_state, events)` — transition logic
  - `EmotionEngine.prompt_fragment()` — text for Gemma prompt injection
  - `EmotionEngine.save() / load()` — persistence

### `goals.py`
Active mission system. Pip always has a current goal that spans multiple ticks.

Goals: `EXPLORING | WATCHING | INVESTIGATING | SOCIALIZING | SEEKING_CHARGER | RESTING | CELEBRATING`

- One active goal at a time with a tick budget
- Each goal provides a `prompt_fragment()` for injection into the idle prompt
- Goals transition when: budget exhausted, event fires, or state forces it
- The autonomy loop passes the current goal to `_build_idle_prompt()`
- Gemma is asked to "advance this goal" rather than "do something random"
- Key API:
  - `GoalEngine.active` — current GoalState
  - `GoalEngine.update(robot_state, events, emotion)` — transition logic
  - `GoalEngine.tick_used()` — decrement budget post-tick
  - `GoalEngine.prompt_fragment()` — text for Gemma prompt injection

### `sentinel.py`
Event-driven interrupt loop. Polls robot state every 2-3 seconds and fires
reactive Gemma calls when significant events are detected.

Watched events:
- `FACE_APPEARED` — face visible in camera (not there before)
- `PICKED_UP` — robot lifted off surface
- `PUT_DOWN` — robot placed back down
- `CUBE_APPEARED` — light cube visible / connected
- `BATTERY_LOW` — voltage crosses low threshold
- `BATTERY_CRITICAL` — voltage crosses critical threshold

- Each event type has a cooldown (minimum time between re-fires)
- Reactive Gemma call uses a targeted prompt ("INTERRUPT: You were just picked up")
- Shares the same safety filter and executor as the autonomy loop
- Does NOT block the autonomy loop — runs as a parallel asyncio task
- Updates emotion and goal engines with events it detects

### `autonomy.py`
The Ralph-Wiggins idle loop. Runs on a configurable timer (default 45s).

Each tick:
1. Read robot state
2. Maybe update vision
3. Update emotion engine with current state
4. Update goal engine with current state + emotion
5. Build idle prompt (includes: time, memory, emotion, goal, vision, prev-tick, no-repeat list)
6. Call Gemma → get action plan
7. Safety filter
8. Execute
9. Post-tick: store what was said in autonomous memory, update tick summary

The idle prompt is the most important prompt in the system. It must give Gemma:
- WHO Pip is (via CHARACTER block in planner SYSTEM)
- WHEN it is (time of day)
- HOW Pip feels (emotion fragment)
- WHAT Pip is trying to do (goal fragment)
- WHAT Pip can see (vision)
- WHAT Pip just did (previous tick summary)
- WHAT NOT to say (recent idle phrases list)

### `planner.py`
Prompt construction and Gemma response parsing.

- `create_plan()` — builds prompt, calls Ollama, parses JSON, safety-filters, returns PlanResponse
- `create_conversation_plan()` — for interactive chat (adds fallback to plain reply if no say action)
- `create_reply_text()` — fallback text generation for pure Q&A
- `SYSTEM` — the full system prompt (CHARACTER + JSON format rules) — constant, loaded once
- `filter_low_value_speech()` — removes filler speech post-parse
- `_clean_say_actions()` — strips stage cues from say text

### `safety.py`
**The hard wall. Do not weaken it.**

- `safety_filter(actions, robot_state)` — removes unsafe actions based on current state
- Motion blocked when: charging, on charger, picked up, cliff detected, obstacle close, low battery
- Drive/turn capped at ±80 mmps / ±90 degrees
- Animation names validated against a fixed allowlist
- Always appends a `stop` action

### `memory.py`
Reads and writes `memory.json`. Shared state for all components.

Structure of `memory.json`:
```json
{
  "facts": ["..."],              // known facts about Rob and the world
  "recent_turns": [...],         // last 40 interactive Rob↔Pip exchanges
  "autonomous_ticks": [...],     // last 8 things Pip said autonomously
  "emotion": {...},              // current emotion state (managed by EmotionEngine)
  "goal": {...}                  // current goal state (managed by GoalEngine)
}
```

- `memory_context(max_turns, max_facts)` — builds text block for Gemma prompts
- `remember_turn(user, assistant)` — stores interactive conversation
- `remember_autonomous_say(tick, text, summary)` — stores idle speech
- `recent_autonomous_says(n)` — returns last n idle phrases for repetition guard

### `robot_io.py`
All Anki Vector SDK calls for reading state and camera.

- State is cached for 8 seconds to avoid hammering the SDK
- Camera capture uses gRPC directly for speed
- Vision description uses Ollama multimodal models (llava, moondream)
- Falls back gracefully if SDK unavailable

### `executors.py`
Two executors: `MockExecutor` (log only) and `VectorSdkExecutor` (real robot).

- Both run through `safety_filter` before any action
- `VectorSdkExecutor.dry_run=True` logs without executing (safe testing)
- Real execution uses gRPC directly via `robot.conn.grpc_interface`
- TTS text is cleaned by `clean_say_text()` before `SayTextRequest`

---

## Gemma Prompt Anatomy

Every call to Gemma has this structure:

```
[SYSTEM]
  CHARACTER block (who Pip is, speech rules, forbidden patterns)
  JSON output format spec
  Allowed action types + examples

[USER PROMPT]
  time_context()           — "Current local time: Thursday 09:15 AM (morning)."
  memory_context()         — known facts + recent Rob↔Pip conversation
  robot_state JSON         — battery, pose, head angle, charger, etc.
  vision observation       — what the camera described (with age)
  emotion.prompt_fragment()— "Current mood: CURIOUS — Pip is..."
  goals.prompt_fragment()  — "Active mission: EXPLORING — Pip is..."
  previous tick summary    — "Previous tick: say(...) → animation:happy → stop"
  no-repeat list           — "Do NOT repeat: ..."
  behavioral instruction   — speak or stay silent this tick, motion allowed or not
```

Temperature for action planning: 0.38 (low — we want consistent, grounded behavior)  
Temperature for reply text: 0.55 (slightly higher — we want natural variation in speech)

---

## API Surface

```
GET  /health              — service status
GET  /                    — HTML dashboard
GET  /events              — last N event log entries

GET  /robot/state         — live robot state snapshot
POST /robot/look          — capture camera + describe

GET  /memory              — full memory.json contents
POST /memory/facts        — add a known fact

GET  /emotion/state       — current Pip mood + metadata
POST /emotion/set         — manually override mood (testing)

GET  /goals/state         — current active goal + tick budget
POST /goals/set           — manually set goal (testing)

GET  /sentinel/status     — event watcher status
POST /sentinel/start      — start event watcher
POST /sentinel/stop       — stop event watcher

GET  /autonomy/status     — loop status + last plan/execute
POST /autonomy/start      — start idle loop
POST /autonomy/stop       — stop idle loop
POST /autonomy/tick       — fire one manual tick

GET  /vision/status       — vision loop status
POST /vision/start        — start scheduled vision loop
POST /vision/stop         — stop it
POST /vision/tick         — one manual vision capture

GET  /listener/status     — mic listener status
POST /listener/start      — start speech listener
POST /listener/stop       — stop it

GET  /voice/status        — wire-pod voice bridge status
POST /voice/start         — start voice bridge
POST /voice/stop          — stop it

POST /plan                — build a plan from user text (no execute)
POST /execute             — execute a pre-built plan
POST /plan_execute        — plan + execute in one call
POST /chat                — interactive chat → plan → execute
```

---

## Execution Modes

Set via `VECTOR_EXECUTION_MODE` env var:

| Mode              | Behavior                                          |
|-------------------|---------------------------------------------------|
| `mock`            | Plans and logs; no SDK calls; safe for dev        |
| `vector-sdk-dry-run` | Connects SDK, reads state; no motion/speech    |
| `vector-sdk`      | Full real execution; requires live robot          |

Default: `mock` (Docker). Host runner defaults to `vector-sdk-dry-run`.

---

## Environment Variables

| Variable               | Default                          | Purpose                        |
|------------------------|----------------------------------|--------------------------------|
| `OLLAMA_BASE_URL`      | `http://host.docker.internal:11434` | Ollama endpoint             |
| `VECTOR_BRAIN_MODEL`   | `gemma4:e4b`                     | Main LLM model                 |
| `VECTOR_VISION_MODEL`  | `moondream:latest,llava:7b`      | Vision models (comma list)     |
| `VECTOR_EXECUTION_MODE`| `mock`                           | Executor mode                  |
| `VECTOR_SERIAL`        | *(unset)*                        | Robot serial number            |
| `VECTOR_MEMORY_PATH`   | `memory.json`                    | Memory file path               |
| `VECTOR_CAPTURE_DIR`   | `captures`                       | Camera capture directory       |

---

## Key Design Decisions

**Why rule-based emotion transitions (not Gemma)?**  
Gemma decides *what to do*. The emotion machine decides *how Pip feels*.  
These are separate concerns. Rule-based transitions are fast, predictable, and  
don't consume context window. Gemma uses the emotion as input, not as output.

**Why a goal system (not pure random idle)?**  
Random idle ticks feel random. Goal-directed behavior feels alive.  
A goal gives consecutive ticks a shared narrative: "Pip is curious about the  
thing that moved on the left side" spans 3 ticks with coherent behavior.

**Why a sentinel interrupt (not just faster tick interval)?**  
Faster ticks burn inference budget constantly. The sentinel only fires when  
something meaningful changes. A face appearing is a discrete event — it should  
be reacted to immediately, not caught on the next 45-second cycle.

**Why keep safety_filter deterministic?**  
LLMs hallucinate. A deterministic safety wall means Pip can never drive off a  
table no matter what Gemma outputs. The wall is not negotiable.

**Why persist emotion/goal to memory.json (not RAM only)?**  
Docker restarts, host crashes. Pip should wake up in the same mood she went to  
sleep in, not reset to baseline every time the container restarts.

---

## What Each AI Agent Should and Shouldn't Touch

### Claude (Anthropic)
**Good at:** Architecture decisions, new module design, prompt engineering,  
integrating new behavioral layers, debugging reasoning issues.  
**Touch freely:** `personality.py`, `emotion.py`, `goals.py`, `sentinel.py`,  
`autonomy.py`, `planner.py`, `memory.py`, `ARCHITECTURE.md`, `COLLABORATION.md`  
**Be careful with:** `safety.py` (only weaken with explicit Rob approval),  
`executors.py` (gRPC calls are tricky — test in dry-run first)

### Codex (OpenAI)
**Good at:** Code completion, refactoring, test writing, implementing  
well-specified functions, finding bugs in logic.  
**Touch freely:** Any `brain/app/*.py` file — follow existing patterns  
**Be careful with:** `safety.py` (same rule), prompt strings in `planner.py`  
(changes affect all Gemma output — test thoroughly)  
**Don't change:** `ARCHITECTURE.md` without leaving a comment about why

### Both agents
- Run `docker compose up --build` to verify the build passes after changes
- Test new planner changes with `POST /plan` in mock mode before real execution
- Do not change `compose.yaml` environment defaults without Rob's sign-off
- Leave `# NOTE(claude):` or `# NOTE(codex):` comments when making non-obvious choices

---

## Testing a Change

```powershell
# Start mock brain
cd "C:\Users\Rob\Documents\New project 5\vector"
docker compose up --build

# Health check
Invoke-RestMethod http://127.0.0.1:8787/health

# Test a plan (no execution)
Invoke-RestMethod http://127.0.0.1:8787/plan -Method Post `
  -ContentType 'application/json' `
  -Body '{"user_text":"idle tick — be curious","robot_state":{"connected":true,"battery_percent":75,"on_charger":false}}'

# Test full execute (dry-run)
Invoke-RestMethod http://127.0.0.1:8787/plan_execute -Method Post `
  -ContentType 'application/json' `
  -Body '{"user_text":"scan the desk","robot_state":{"connected":true,"on_charger":false},"dry_run":true}'

# Check emotion state
Invoke-RestMethod http://127.0.0.1:8787/emotion/state

# Check goal state
Invoke-RestMethod http://127.0.0.1:8787/goals/state

# Manual autonomy tick
Invoke-RestMethod http://127.0.0.1:8787/autonomy/tick -Method Post `
  -ContentType 'application/json' `
  -Body '{"enabled":true,"dry_run":true,"interval_seconds":45}'
```

---

## Roadmap

### Completed
- [x] FastAPI brain service with safety layer and mock/SDK executor
- [x] Gemma 4 integration via Ollama
- [x] Pip personality injected into all planner prompts
- [x] Idle autonomy loop (Ralph-Wiggins style)
- [x] Autonomous speech memory + repetition guard
- [x] Time-of-day context in all prompts
- [x] Previous-tick action tracking
- [x] TTS stage-cue cleanup ([chirp] etc.)
- [x] Wire-pod voice bridge (intent routing)
- [x] Microphone listener (Whisper STT)
- [x] Vision loop (moondream / llava description)

### Completed (continued)
- [x] Emotional state machine (`emotion.py`) — 7 states, rule-based, persists to memory.json
- [x] Active goal system (`goals.py`) — 7 goals, tick budgets, priority preemption
- [x] Event-driven sentinel loop (`sentinel.py`) — 2s reflex, 8 event types, per-event cooldowns
- [x] Emotion + goal integration into autonomy loop — prompt injection, speak bias, tick_used
- [x] New API routes: `/emotion/*`, `/goals/*`, `/sentinel/*`
- [x] Dev-unlocked control architecture documented

### Future
- [ ] Web search tool routed through host layer (`POST /ask-host`)
- [ ] Face recognition (remember specific people)
- [ ] Multi-session narrative memory (what happened yesterday)
- [ ] TTS voice personality layer (Pip's actual voice, not Vector stock)
- [ ] Policy training from logged interaction data
