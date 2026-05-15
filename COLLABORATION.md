# AI Collaboration Guide

This project is actively developed by two AI agents — **Claude** (Anthropic) and **Codex** (OpenAI) —  
working alongside Rob. This document is the handshake protocol that keeps both agents aligned.

**Read `ARCHITECTURE.md` first.** This document assumes you understand the system.

---

## Current State (2026-05-14)

### ✅ Complete and working

| Subsystem | File(s) | Notes |
|-----------|---------|-------|
| Pip personality + forbidden-speech filter | `personality.py`, `planner.py` | Injected into every Gemma prompt |
| Time-of-day context | `personality.py` | In every prompt |
| Autonomous memory / repetition guard | `memory.py`, `autonomy.py` | Previous-tick + recent-says in idle prompt |
| TTS stage-cue cleanup | `personality.py`, `executors.py` | Belt-and-suspenders at planner + executor layer |
| WirePod voice bridge | `voice_bridge.py`, `main.py` | Routes intents → Gemma via `_run_chat` |
| WirePod transcript endpoint | `main.py` (`/wirepod/transcript`) | Fire-and-forget background task; Codex-authored |
| Whisper STT listener | `listener.py`, `audio_capture.py` | Continuous VAD + faster-whisper pipeline |
| Vision loop | `vision_loop.py`, `robot_io.py` | moondream primary, llava fallback |
| EmotionEngine | `emotion.py` | 7-state mood machine; persistent to `memory.json` |
| GoalEngine | `goals.py` | 7-goal mission system; tick budgets + preemption |
| Sentinel | `sentinel.py` | 2s poll loop; 8 event types; per-event cooldowns |
| Emotion + goal integration | `autonomy.py`, `main.py` | Prompt fragments + speak-bias wired into idle tick |
| `face_detected` / `cube_detected` in RobotState | `schemas.py`, `robot_io.py` | SDK reads populate sentinel events |
| Robot state caching (cooldown mode) | `robot_io.py` | 5-min cache when charging/sleeping — Codex-authored |
| All API routes | `main.py` | `/autonomy/*`, `/voice/*`, `/emotion/*`, `/goals/*`, `/sentinel/*`, `/listener/*` |
| `listen_after_speech` wiring | `autonomy.py`, `main.py` | `_autonomy_listen_callback` opens 8s Whisper window after Pip speaks |
| `ListenAction` → local Whisper (not cloud STT) | `executors.py` | `AppIntentRequest` replaced with no-op; actual STT via callback |
| Thermal auto-slowdown | `autonomy.py` (`_thermal_interval`) | <20% batt → 90s interval; <10% → 180s |

| Memory consolidation loop | `autonomy.py` (`_maybe_consolidate_memory`) | Every 10 ticks, Gemma extracts 0-3 durable facts from `recent_turns` → `memory.json["facts"]` |
| Sentinel → listen_callback | `sentinel.py`, `schemas.py`, `main.py` | `config.listen_after_speech=True` opens Whisper reply window after reactive speech |
| VoiceBridge → listen_callback | `voice_bridge.py`, `schemas.py`, `main.py` | Same pattern — `config.listen_after_speech=True` opens Whisper window after WirePod speech |

### 🔲 Pending / Not yet implemented

| Feature | Where | Notes |
|---------|-------|-------|
| `listen_after_speech` toggle in API/dashboard | `schemas.py` | Config fields exist on all three subsystems — no dashboard toggle yet, but easily added |
| LanceDB / vector memory (optional future) | n/a | Discussed: premature given Gemma 4's 128K context. Not a priority until `memory.json` hits size limits. |

### ⚠️ Known risks / verify before production

| Risk | Details |
|------|---------|
| Codex helper functions | `_is_explicit_robot_command`, `_repair_command_actions`, `_conversation_only_actions` in `main.py` were approximated after file truncation. Test the WirePod voice path end-to-end. |
| TTS echo → Whisper re-transcription | Pip's TTS audio may be picked up by the microphone. The 3s post-route mute in `_autonomy_listen_callback` should prevent it, but test with real hardware. Increase `ListenerConfig.mute_after_route_seconds` if echo persists. |
| SDK connection per-tick overhead | Each autonomy tick and each `VectorSdkExecutor` call opens a new SDK connection. Thermal slowdown helps at low battery, but watch CPU/network on the host. |
| Memory consolidation noise | Gemma may extract low-quality or slightly wrong "facts". Review `memory.json["facts"]` periodically; use `DELETE /memory/facts` (if added) or edit the file directly. |

## What Is Being Built Now

Nothing in progress. Next feature is **Conversation Mode** — full design spec below.

---

## Design Spec: Conversation Mode (ConversationSession)

### The Problem

The current system is **stateless**. Every interaction is an independent one-shot:

```
wake word → WirePod sends transcript → Gemma → execute → done
```

`_autonomy_listen_callback` is a single-iteration conversation stub — it opens
one 8s window after Pip speaks, routes one reply, and returns. That's it.

Rob's request: generalize that into a **persistent state machine** where the system
stays in an active conversation across multiple exchanges without requiring a
wake word after the first one.

```
COMMAND MODE (current):
  wake word → action → stop listening

CONVERSATION MODE (target):
  wake word → response → listen → reply → listen → reply → ... → timeout → idle
```

---

### State Machine

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │  IDLE                                                               │
 │  Listening only for wake word (WirePod / voice_bridge)             │
 │  Whisper transcripts are IGNORED in this state                     │
 └──────────────────────┬──────────────────────────────────────────────┘
                        │ on_wake_word(text)
                        ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │  ENGAGED                                                            │
 │  Active conversation. No wake word required.                        │
 │  Whisper transcripts route to Gemma automatically.                 │
 │  Timeout task running (8s silence → COOLDOWN)                      │
 │                                                                     │
 │  on_transcript(text):                                               │
 │    if is_exit_phrase(text) → COOLDOWN                              │
 │    else → THINKING                                                  │
 │  on_timeout() → COOLDOWN                                            │
 └──────────────────────┬──────────────────────────────────────────────┘
                        │
              ┌─────────▼─────────┐
              │     THINKING       │  (awaiting Gemma response)
              │  await Gemma call  │
              └─────────┬─────────┘
                        │ plan returned
                        ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │  SPEAKING                                                           │
 │  Executing plan (TTS + non-motion actions)                         │
 │  If plan has motion → ACTION first, then back here                 │
 │  After execution: emit conversational cue (15% probability)        │
 │  Reset timeout task                                                 │
 │  → ENGAGED                                                          │
 └─────────────────────────────────────────────────────────────────────┘

 ACTION (sub-state within SPEAKING, not a separate wait state):
   Motion actions executed before TTS.
   After motion + TTS complete → ENGAGED.

 ┌─────────────────────────────────────────────────────────────────────┐
 │  COOLDOWN                                                           │
 │  No reply in listen window, or exit phrase detected                │
 │  Optional: brief goodbye phrase (20% probability) or animation     │
 │  wait 1s → IDLE                                                     │
 └─────────────────────────────────────────────────────────────────────┘
```

**The key rule:**
```python
wake_word_required = (session.state == ConversationState.IDLE)
```

---

### New File: `conversation_session.py`

```python
from enum import Enum

class ConversationState(str, Enum):
    IDLE      = "IDLE"
    ENGAGED   = "ENGAGED"
    THINKING  = "THINKING"
    SPEAKING  = "SPEAKING"
    COOLDOWN  = "COOLDOWN"

class ConversationSession:
    """
    State machine for ongoing Pip–Rob conversations.
    Coordinates: wake word detection, Whisper STT, Gemma calls, execution.

    Usage in main.py:
        session = ConversationSession(
            model=MODEL,
            ollama_base_url=OLLAMA_BASE_URL,
            execution_mode=EXECUTION_MODE,
            vector_serial=VECTOR_SERIAL,
            emotion_engine=emotion_engine,
            goal_engine=goal_engine,
        )
        # Wire:
        session.listener = listener
        # Then replace _route_listener_transcript with session.on_transcript
        # Replace voice_bridge intent handling with session.on_wake_word
    """

    # Public state
    state: ConversationState  # current state
    session_id: str | None    # UUID for current conversation session (None when IDLE)
    turns: int                # turns in current session
    engaged_at: float | None  # timestamp when this session began
    last_activity: float | None  # timestamp of last transcript
    last_action_summary: str | None  # e.g. "say('Okay') → drive → stop"
    last_error: str | None

    # Config
    silence_timeout_s: float = 8.0     # seconds before COOLDOWN
    listen_window_s: float = 8.0       # same — one config value controls both
    cue_probability: float = 0.15      # how often to emit a conversational cue
    goodbye_probability: float = 0.20  # how often to say goodbye on COOLDOWN

    # Core methods (called from outside)
    async def on_wake_word(self, text: str) -> None:
        """
        Called by voice_bridge._handle_intent when WirePod fires.
        Transitions IDLE → ENGAGED and processes the first turn immediately.
        Strips "Hey Pip" / "Vector" from the start of text if present.
        """

    async def on_transcript(self, text: str, payload: dict) -> None:
        """
        Called by _route_listener_transcript for every Whisper transcript.
        If state == IDLE → return immediately (ignored).
        If is_exit_phrase(text) → COOLDOWN.
        Else → THINKING → SPEAKING → ENGAGED.
        Resets the timeout task.
        """

    def is_active(self) -> bool:
        """Return True when state != IDLE. Used by autonomy to suppress ticks."""
        return self.state != ConversationState.IDLE

    def status(self) -> dict:
        """Return current state as a serializable dict for GET /conversation/status."""

    # Internal methods
    async def _handle_turn(self, text: str) -> None:
        """
        The core turn loop:
          1. state → THINKING
          2. Build ENGAGED prompt (includes session context, last action)
          3. await create_conversation_plan()
          4. state → SPEAKING
          5. await executor.execute(plan)
          6. remember_turn(text, say_text)
          7. goal_engine.notify_positive_interaction()
          8. Update emotion (SOCIALIZING / EXCITED)
          9. Emit conversational cue (if cue_probability fires)
          10. Reset timeout task
          11. state → ENGAGED
        """

    async def _cooldown(self, reason: str) -> None:
        """
        Transition to COOLDOWN:
        - Cancel timeout task
        - Optional goodbye phrase/animation (goodbye_probability)
        - Wait 1s (don't leave listener open)
        - state → IDLE
        - Clear session_id, turns, last_action_summary
        - Log add_event("conversation_end", {reason, turns, duration})
        """

    async def _reset_timeout(self) -> None:
        """
        Cancel any existing timeout task and start a new one.
        Called after every transcript received and after every Pip response.
        On fire: await self._cooldown("silence timeout")
        """

    def _build_engaged_prompt(self, text: str) -> str:
        """
        Build the Gemma prompt for an ENGAGED-state turn.
        This is different from _run_chat's one-shot prompt — it includes:
          - "Active conversation, turn N"
          - last_action_summary (what Pip just did)
          - memory_context() (full turn history)
          - robot_state
          - The instruction: 'Rob's follow-up: "{text}" — continue naturally.'
          - Do NOT say "I just said" or narrate the last action — just react
          - Keep response to 3-10 words unless the question demands more
        """

    def _is_exit_phrase(self, text: str) -> bool:
        """
        Local check — no LLM. Check for:
        "stop", "sleep", "go to sleep", "that's all", "nevermind",
        "never mind", "goodbye", "bye", "thanks bye", "that's enough",
        "quiet", "be quiet", "shut up"
        Uses substring matching on lowercased text.
        """

    def _conversational_cue(self) -> str | None:
        """
        With probability cue_probability (default 15%), return a brief
        in-character listening cue. Otherwise return None (silent listening).
        Examples:
          "Anything else?"
          "Want me to keep going?"
          "Like this?"
          "Should I turn too?"
          "I'm listening."
        These are LOCAL phrases — no LLM call.
        Pip mostly listens silently after speaking (85% of the time).
        """

    def _strip_wake_word(self, text: str) -> str:
        """Strip 'Hey Pip', 'Hey Vector', 'Pip,' from start of first-turn text."""
```

---

### Why the ENGAGED Prompt Is Different (the magic)

With the current one-shot `_run_chat`, when Rob says "A little more," Gemma gets:

```
Rob: "A little more."
```

Gemma has no idea what "a little more" refers to. It might respond generically.

With the ENGAGED prompt, Gemma gets:

```
Active conversation — turn 2.
Last action: say("Okay, moving forward a little.") → drive:30mmps → stop
Rob's follow-up: "A little more."
Continue naturally. You know the context.
```

Now Gemma correctly understands this is a follow-up movement request. **This is the moment Pip goes from a voice assistant to a conversational companion.**

---

### Integration: What Changes in Each Existing File

#### `main.py` (most changes)

1. Instantiate `ConversationSession` alongside existing singletons
2. Replace `_route_listener_transcript` with one that checks session state:
   ```python
   async def _route_listener_transcript(text, payload, config):
       if conversation_session.is_active():
           await conversation_session.on_transcript(text, payload)
       else:
           # Original behavior: only route if auto_route enabled
           if config.auto_route:
               await _run_chat(...)
   ```
3. Expose routes: `GET /conversation/status`, `POST /conversation/reset`
4. Remove `_autonomy_listen_callback` (replaced by ConversationSession)
5. Remove `autonomy.listen_callback`, `sentinel.listen_callback`, `voice_bridge.listen_callback` (session handles all listening)

#### `voice_bridge.py`

`_handle_intent` currently does: plan → execute → maybe listen_callback.

New behavior:
```python
async def _handle_intent(self, payload: dict) -> None:
    text = _intent_to_prompt(payload)
    # Notify session instead of doing everything here
    if self.conversation_session is not None:
        await self.conversation_session.on_wake_word(text)
    else:
        # Fallback: original behavior if session not wired
        ... (existing code) ...
```

Add `self.conversation_session: ConversationSession | None = None` to `__init__`.
Wire in `main.py`: `voice_bridge.conversation_session = conversation_session`

#### `autonomy.py` (`_tick_once`)

Add a guard at the top of `_tick_once()`:
```python
async def _tick_once(self) -> None:
    self.ticks += 1
    # Don't interrupt an active Rob conversation with idle behavior
    if self._is_conversation_active and self._is_conversation_active():
        add_event("autonomy_skip", {"tick": self.ticks, "reason": "conversation active"})
        return
    # ... rest of existing code ...
```

Add `self._is_conversation_active: Callable[[], bool] | None = None` to `__init__`.
Wire in `main.py`: `autonomy._is_conversation_active = conversation_session.is_active`

#### `sentinel.py` (`_fire_reactive_tick`)

Before firing a reactive tick, check if a conversation is active:
```python
async def _fire_reactive_tick(self, event: str, robot_state: RobotState) -> None:
    # Don't interrupt Rob talking to Pip
    if self._is_conversation_active and self._is_conversation_active():
        add_event("sentinel_skip", {"event": event, "reason": "conversation active"})
        return
    # ... existing code ...
```

Add `self._is_conversation_active: Callable[[], bool] | None = None` to `__init__`.
Wire in `main.py`: `sentinel._is_conversation_active = conversation_session.is_active`

Also: after sentinel fires reactive speech (not suppressed), optionally enter ENGAGED:
```python
# If sentinel speaks and conversation_session is wired, enter ENGAGED for reply
if say_text and self.config.listen_after_speech and self.conversation_session:
    await self.conversation_session.on_wake_word(f"[sentinel:{event}] {say_text}")
```

Wire: `sentinel.conversation_session = conversation_session`

#### `schemas.py`

Add `ConversationSessionConfig` and `ConversationSessionStatus`:

```python
class ConversationSessionConfig(BaseModel):
    silence_timeout_s: float = Field(default=8.0, ge=3.0, le=30.0)
    listen_window_s: float = Field(default=8.0, ge=3.0, le=30.0)
    cue_probability: float = Field(default=0.15, ge=0.0, le=1.0)
    goodbye_probability: float = Field(default=0.20, ge=0.0, le=1.0)
    allow_motion: bool = True   # allow movement actions in ENGAGED state
    require_explicit_command_for_motion: bool = True  # use _is_explicit_robot_command gate

class ConversationSessionStatus(BaseModel):
    state: str
    session_id: str | None = None
    turns: int = 0
    engaged_at: float | None = None
    last_activity: float | None = None
    last_action_summary: str | None = None
    last_error: str | None = None
```

---

### Conversational Cues: Sparse by Design

After SPEAKING→ENGAGED, the system mostly listens silently. Cues fire with
`cue_probability = 0.15` (15%). These are **local hardcoded phrases**, NOT LLM calls.

Good cues (short, open-ended, in-character):
- "Anything else?"
- "Want me to keep going?"
- "Like this?"
- "Should I turn too?"
- "I'm listening."

Bad cues (do not add these):
- Anything that sounds like a chatbot prompt
- Anything over 5 words
- Anything that narrates Pip's internal state

These are NOT said as a "say" action via the executor — they are injected
as a tiny TTS call AFTER execution completes and BEFORE the listen window opens,
so they don't disrupt the plan structure.

---

### Exit Phrase Detection

Local substring matching. No LLM. Runs before routing to Gemma.

Phrases that trigger immediate COOLDOWN:
```python
_EXIT_PHRASES = {
    "stop", "sleep", "go to sleep", "that's all", "that is all",
    "nevermind", "never mind", "goodbye", "bye", "thanks bye",
    "thank you bye", "that's enough", "that is enough",
    "quiet", "be quiet", "shut up", "pause",
}
```

Check: `any(phrase in text.lower() for phrase in _EXIT_PHRASES)`

---

### Motion Safety in ENGAGED Mode

When ENGAGED (no wake word), motion is more dangerous — Rob might just be chatting.

Guard: if `config.require_explicit_command_for_motion` is True (default):
- Run `_is_explicit_robot_command(text)` before allowing drive/turn/behavior actions
- If text doesn't match explicit command keywords, use `create_conversation_plan`
  (which prefers speech over motion) rather than `create_plan`
- If plan still produces motion from `create_conversation_plan`, strip it
  (set `action.type not in {"drive", "turn"}`)

This is already half-implemented in `_route_wirepod_transcript` via
`_conversation_only_actions()`. The session should use the same logic.

---

### Double-Routing Prevention

The listener's `auto_route` flag must be `False` when using ConversationSession.
The session handles all routing via `on_transcript()`.

If both `auto_route=True` AND ConversationSession are active,
every transcript would be processed twice (once by auto_route, once by session).

Fix in `main.py`:
- When starting the listener for use with ConversationSession: always start with `auto_route=False`
- ConversationSession's `on_transcript` IS the router

---

### Autonomy Suppression Timing

The `_is_conversation_active` check is at the TOP of `_tick_once()`. If a tick
is mid-flight when ENGAGED starts, it runs to completion (we don't cancel it).
Only the NEXT tick is suppressed. This is acceptable — one extra idle action
occasionally overlapping with ENGAGED is a minor UX issue, not a bug.

---

### Memory and Context Across Turns

Each ENGAGED turn calls:
1. `remember_turn(text, say_text)` — persists to `memory.json["recent_turns"]`
2. `goal_engine.notify_positive_interaction()` — keeps SOCIALIZING goal active
3. `emotion_engine.update(robot_state, events=[])` — ENGAGED state should bias
   toward EXCITED or PLAYFUL; the session should call `emotion_engine.force_state("EXCITED")`
   on IDLE→ENGAGED transition, and let natural decay handle the return

The ENGAGED prompt uses `memory_context(max_turns=6)` — more turns than idle
(which uses 3) because follow-up context matters more in active conversation.

---

### Implementation Order

1. `schemas.py` — add `ConversationSessionConfig`, `ConversationSessionStatus`
2. `conversation_session.py` — create the class (skeleton + state transitions first)
3. `main.py` — instantiate, replace `_route_listener_transcript`, add routes
4. `voice_bridge.py` — `_handle_intent` delegates to session
5. `autonomy.py` — add `_is_conversation_active` guard
6. `sentinel.py` — add suppression check + optional ENGAGED entry
7. End-to-end test: wake → reply → follow-up → follow-up → timeout → idle

**Do NOT** implement steps 5-6 until steps 1-4 are tested. Autonomy suppression
and sentinel collision handling are polish, not MVP.

---

### Invariants (do not break these)

- `safety.py` is never touched — safety filter applies inside conversation turns same as always
- `memory.py` is never touched — ConversationSession is a consumer, not a writer of schema
- ConversationSession never calls `create_plan` directly for non-command turns — always `create_conversation_plan`
- ConversationSession never executes without going through `safety_filter` (handled by planner + executor)
- A turn in THINKING state does not time out — only ENGAGED state has a silence timeout
- If Gemma call fails: log error, return to ENGAGED with a local fallback phrase (don't crash session)

---

## File Ownership Conventions

These are soft conventions, not locks. Both agents can touch any file.  
But this is where each agent has the most context:

| File              | Primary context                                          |
|-------------------|----------------------------------------------------------|
| `personality.py`  | Character decisions — prefer Claude for prompt changes   |
| `emotion.py`      | New module — see design spec below                       |
| `goals.py`        | New module — see design spec below                       |
| `sentinel.py`     | New module — see design spec below                       |
| `planner.py`      | Prompt engineering — prefer Claude for SYSTEM changes    |
| `autonomy.py`     | Loop logic — either agent                                |
| `safety.py`       | **Do not weaken** — only harden; get Rob's sign-off      |
| `executors.py`    | SDK calls — test dry-run before real; either agent       |
| `memory.py`       | Schema changes affect all readers — document additions   |
| `schemas.py`      | Pydantic models — keep consistent with actual usage      |
| `main.py`         | Route registration — follow existing pattern             |

---

## How to Leave Context for the Other Agent

When you make a non-obvious decision, leave a comment:

```python
# NOTE(claude): Using rule-based transitions here rather than asking Gemma —
# emotion state must be fast and predictable, not inference-dependent.

# NOTE(codex): This cooldown prevents sentinel from firing the same event
# type more than once per 30s, even if state oscillates.
```

When you complete a significant chunk of work, update the **Completed** list  
in `ARCHITECTURE.md` and add a brief entry to `CHANGELOG.md` (create it if missing).

---

## Design Specs for In-Progress Modules

### `emotion.py` — Emotional State Machine

**States:**
```
CURIOUS   — default; exploration, head tilts, scanning
CONTENT   — relaxed; occasional positive animations
ALERT     — something noticed; heightened attention, less motion
PLAYFUL   — high energy; more motion, more speech, happy animations
CAUTIOUS  — uncertain; slower, stay still, confused animations
TIRED     — low battery or long session; minimal actions, quiet
EXCITED   — face/cube/interaction detected; celebrate or veryHappy animation
```

**Transition triggers (rule-based, no LLM):**
- `battery_percent < 15` → TIRED (from any state)
- `battery_percent < 35` and not TIRED → CAUTIOUS (nudge)
- `battery_percent > 80` and TIRED → CONTENT
- `face_detected event` → ALERT → (after 2 ticks) → EXCITED if interaction, else CURIOUS
- `picked_up event` → EXCITED
- `cube_appeared event` → ALERT then PLAYFUL
- `no interaction for 10+ ticks` → CAUTIOUS (getting lonely)
- `recent positive interaction` → CONTENT or PLAYFUL
- `time is night (22:00–06:00)` → nudge toward TIRED
- Natural decay: every 5 ticks without a trigger, nudge toward CURIOUS (baseline)

**Mood biases for the autonomy loop:**
```python
MOOD_SPEAK_BIAS = {
    "CURIOUS":  0.0,    # no change to base speak_probability
    "CONTENT":  -0.05,  # slightly quieter
    "ALERT":    +0.15,  # more likely to comment
    "PLAYFUL":  +0.25,  # most talkative
    "CAUTIOUS": -0.10,  # quieter
    "TIRED":    -0.20,  # much quieter
    "EXCITED":  +0.30,  # very talkative
}

MOOD_PREFERRED_ANIMATIONS = {
    "CURIOUS":  ["thinking", "confused"],
    "CONTENT":  ["happy"],
    "ALERT":    ["thinking"],
    "PLAYFUL":  ["happy", "veryHappy", "celebrate"],
    "CAUTIOUS": ["confused"],
    "TIRED":    ["sad"],
    "EXCITED":  ["veryHappy", "celebrate", "love"],
}
```

**Prompt fragment (what Gemma sees):**
```
Current mood: PLAYFUL
Pip is feeling energetic and expressive right now.
Preferred expression: happy or celebrate animations. More speech is natural.
Avoid: slow or sad animations unless story demands it.
```

**Persistence:** Stored in `memory.json["emotion"]` as:
```json
{
  "state": "CURIOUS",
  "ticks_in_state": 3,
  "last_transition_reason": "battery recovered above 80%",
  "updated_at": "2026-05-14T09:15:00+00:00"
}
```

**Key API:**
```python
class EmotionEngine:
    state: MoodState          # current mood enum
    ticks_in_state: int       # how long in this mood
    def update(robot_state, events: list[str]) -> str | None  # returns transition reason or None
    def speak_probability_bias() -> float                      # additive bias for autonomy loop
    def prompt_fragment() -> str                               # text for Gemma prompt
    def preferred_animations() -> list[str]                    # bias list for idle fallback
    def save() -> None
    def load() -> None
```

---

### `goals.py` — Active Mission System

**Goals:**
```
EXPLORING      — scanning desk, looking around; default when nothing else active
WATCHING       — focused attention on Rob specifically; triggered by face event
INVESTIGATING  — something in vision worth examining; triggered by novel vision item
SOCIALIZING    — Rob is present; maximize engagement; triggered by active conversation
SEEKING_CHARGER— low battery; find and dock; overrides all others below critical
RESTING        — very low battery or calm mode; minimal action
CELEBRATING    — just had a positive moment; 2-3 tick burst of happiness
```

**Goal structure:**
```python
@dataclass
class GoalState:
    name: str              # goal identifier
    description: str       # what Pip is trying to do (for Gemma prompt)
    priority: int          # 0=lowest, 10=highest (SEEKING_CHARGER = 10)
    tick_budget: int       # how many ticks before natural transition
    ticks_used: int        # current progress
    trigger_event: str     # what caused this goal
    started_at: str        # ISO timestamp
```

**Transition rules:**
- SEEKING_CHARGER (priority 10) — fires when battery < 20%, overrides everything
- RESTING (priority 9) — fires when charging or calm_power_mode
- SOCIALIZING (priority 7) — fires when a conversation just happened (< 2 min ago)
- CELEBRATING (priority 6) — fires after positive interaction; budget = 2 ticks
- WATCHING (priority 5) — fires on face_detected event; budget = 4 ticks
- INVESTIGATING (priority 4) — fires on novel vision description; budget = 3 ticks
- EXPLORING (priority 1) — default fallback; always available; budget = 8 ticks

**Prompt fragment (what Gemma sees):**
```
Active mission: EXPLORING (tick 3 of 8)
Pip is scanning the desk environment — looking around, noticing things,
staying curious about what's nearby. Good actions: look_around behavior,
head movement, quiet observation. No need to speak unless something is noticed.
```

**Key API:**
```python
class GoalEngine:
    active: GoalState
    def update(robot_state, events: list[str], emotion: MoodState) -> None
    def tick_used() -> None           # call after each autonomy tick
    def prompt_fragment() -> str      # text for Gemma prompt
    def save() -> None
    def load() -> None
```

---

### `sentinel.py` — Event-Driven Interrupt Loop

**Architecture:**
```
[Sentinel asyncio task — runs every 2s]
    │
    ├── read_robot_snapshot() (uses cached state, max_age=3s)
    │
    ├── compare to _last_known_state
    │
    ├── detect events: face appeared? picked up? battery threshold?
    │
    ├── for each new event (if not in cooldown):
    │       ├── update emotion engine
    │       ├── update goal engine
    │       └── fire reactive Gemma call → safety filter → execute
    │
    └── store _last_known_state
```

**Watched events and cooldowns:**
```python
SENTINEL_EVENTS = {
    "FACE_APPEARED":      {"cooldown_s": 30,  "priority": 8},
    "FACE_LOST":          {"cooldown_s": 60,  "priority": 3},
    "PICKED_UP":          {"cooldown_s": 10,  "priority": 9},
    "PUT_DOWN":           {"cooldown_s": 15,  "priority": 7},
    "CUBE_APPEARED":      {"cooldown_s": 60,  "priority": 5},
    "BATTERY_LOW":        {"cooldown_s": 300, "priority": 8},
    "BATTERY_CRITICAL":   {"cooldown_s": 120, "priority": 10},
    "OBSTACLE_APPEARED":  {"cooldown_s": 20,  "priority": 6},
}
```

**Reactive prompts (what Gemma sees instead of idle tick):**
```
INTERRUPT EVENT: Face appeared in camera view.
You are Pip. Something just changed — a face appeared.
React immediately and naturally. This is a live moment, not an idle tick.
Do not narrate that you noticed something — just react with action.
Suggested: turn toward, head tilt, greeting animation, short phrase if appropriate.
One or two actions max, then stop.
```

**Key API:**
```python
class Sentinel:
    def __init__(model, ollama_base_url, execution_mode, vector_serial,
                 emotion_engine, goal_engine): ...
    async def start(config: SentinelConfig) -> SentinelStatus
    async def stop() -> SentinelStatus
    def status() -> SentinelStatus
    # Internal:
    async def _poll_loop() -> None
    async def _fire_reactive_tick(event: str, robot_state: RobotState) -> None
    def _detect_events(current: dict, previous: dict) -> list[str]
    def _build_reactive_prompt(event: str, robot_state: RobotState) -> str
```

---

## Memory Schema (full current structure)

`memory.json` after all planned additions:
```json
{
  "facts": ["string", "..."],
  "recent_turns": [
    {"ts": "ISO", "user": "string", "assistant": "string"}
  ],
  "autonomous_ticks": [
    {"ts": "ISO", "tick": 7, "say": "string", "actions": "string"}
  ],
  "emotion": {
    "state": "CURIOUS",
    "ticks_in_state": 3,
    "last_transition_reason": "string",
    "updated_at": "ISO"
  },
  "goal": {
    "name": "EXPLORING",
    "description": "string",
    "priority": 1,
    "tick_budget": 8,
    "ticks_used": 3,
    "trigger_event": "string",
    "started_at": "ISO"
  }
}
```

---

## Prompt Structure Reference

For any Gemma call, the full context looks like this:

```
[SYSTEM — planner.py SYSTEM constant]
CHARACTER block:
  - Pip identity
  - Speech rules
  - Forbidden patterns
  - JSON output format
  - Action types + examples

[USER PROMPT — built by _build_idle_prompt() or reactive prompt builder]
Line 1:  "Idle embodiment tick #N. You are Pip, a small local robot on Rob's desk."
Line 2:  time_context()           → "Current local time: Thursday 09:15 AM (morning)."
Line 3:  "Vibe: <config.vibe>"
Line 4:  emotion.prompt_fragment()→ "Current mood: CURIOUS — ..."
Line 5:  goals.prompt_fragment()  → "Active mission: EXPLORING (tick 3 of 8) — ..."
Line 6:  memory_context()         → facts + recent conversation
Line 7:  "Live robot state: {...}"
Line 8:  vision description       → "Visual observation (moondream, 12s ago): ..."
Line 9:  previous tick summary    → "Previous tick: say(...) → animation:happy → stop"
Line 10: no-repeat list           → "Do NOT repeat: ..."
Line 11: behavioral rules         → speak/silent, motion allowed/not
Line 12: "Keep it under 3 actions and end with stop."
```

---

## Changelog

### 2026-05-14 session 4 (Claude)
- Memory consolidation loop: `_maybe_consolidate_memory()` in `autonomy.py` runs every 10 ticks; asks Gemma to extract 0-3 durable facts from recent conversation and persists to `memory.json["facts"]` via `add_fact()`
- Sentinel listen_callback: `SentinelConfig.listen_after_speech` added; `sentinel.listen_callback` injected from `main.py`; fires after reactive speech in `_fire_reactive_tick()`
- VoiceBridge listen_callback: `VoiceBridgeConfig.listen_after_speech` added; `voice_bridge.listen_callback` injected from `main.py`; fires after `_handle_intent()` speech
- All three subsystems (autonomy, sentinel, voice_bridge) share one `_autonomy_listen_callback` — single wiring point in `main.py`
- System is now feature-complete for Rob's stated goals

### 2026-05-14 session 3 (Claude)
- Wired `listen_after_speech` turn-based conversation loop:
  - `autonomy.py`: added `listen_callback` injection point + `_thermal_interval()` + fires callback after speech
  - `main.py`: added `_autonomy_listen_callback` (8s poll window → Whisper → `_run_chat`); wired into autonomy singleton
  - `executors.py`: `ListenAction` is now a documented no-op; all STT goes through local Whisper, not `AppIntentRequest`
- Thermal auto-slowdown: `_thermal_interval()` in autonomy.py slows loop to 90s at <20% battery, 180s at <10%
- Updated `COLLABORATION.md` with full done/pending/risk table so Codex or Claude can resume without context

### 2026-05-14 session 2 (Claude)
- Created `personality.py` — Pip identity module, TTS cleanup, forbidden speech detection
- Rewrote `planner.py` SYSTEM prompt — Pip identity injected into all Gemma calls
- Rebuilt `autonomy.py` idle prompt — time, more memory, previous-tick, repetition guard
- Added `remember_autonomous_say()` / `recent_autonomous_says()` to `memory.py`
- Updated vision prompt (moondream gets same rich prompt as llava)
- Added TTS cleanup to `executors.py` (belt-and-suspenders)
- Cleaned `memory.json` — removed poisoned "I had a thought, but I kept it safe." turns
- Created `ARCHITECTURE.md` and `COLLABORATION.md`
- Added `emotion.py` — 7-state mood engine with rule-based transitions, decay, persistence
- Added `goals.py` — 7-goal active mission system with tick budgets, priority preemption
- Added `sentinel.py` — 2s async poll loop; detects 8 event types; fires reactive Gemma calls; per-event cooldowns
- Extended `schemas.py` — added `face_detected`, `cube_detected` to RobotState; added `SentinelConfig`, `SentinelStatus`
- Extended `robot_io.py` — `_read_robot_snapshot_sync` now populates `face_detected` / `cube_detected` from SDK
- Wired `autonomy.py` — emotion + goal engines update each tick; fragments injected into idle prompt; speak bias applied; `goal_engine.tick_used()` called post-tick
- Wired `main.py` — shared `emotion_engine` + `goal_engine` singletons; `Sentinel` instantiated; 6 new routes: `/emotion/state`, `/emotion/set`, `/goals/state`, `/goals/set`, `/sentinel/status`, `/sentinel/start`, `/sentinel/stop`; `goal_engine.notify_positive_interaction()` fires after each successful chat turn

### 2026-05-15 session 2 (Codex)
- Published a clean public no-license snapshot to `https://github.com/RobVanProd/pip-vector-autonomy`, excluding local-only runtime binaries, logs, captures, downloads, venvs, and voice-reference files.
- Implemented Conversation Mode MVP through step 4 of the design spec: added `conversation_session.py`, `ConversationSessionConfig`, `ConversationSessionStatus`, `/conversation/status`, `/conversation/config`, `/conversation/reset`, WirePod first-turn routing, voice bridge delegation, and listener pending polling while engaged.
- ConversationSession handles IDLE -> ENGAGED -> THINKING -> SPEAKING -> ENGAGED -> COOLDOWN -> IDLE, strips wake words, detects local exit phrases, uses explicit-command gating for motion, and times out after silence.
- Dry-run smoke test: `/wirepod/transcript` with `Hey Pip, what is my name?` started a session, answered Rob's name, and returned to IDLE on silence timeout.
- Per the spec, autonomy/sentinel suppression steps were not implemented yet; test steps 1-4 more before adding suppression polish.

### 2026-05-15 session 3 (Codex)
- Added external validation camera support for `Logi C615 HD WebCam` using ffmpeg/DirectShow. New routes: `/external-camera/status`, `/external-camera/capture`, `/external-camera/latest.jpg`, `/validation/pip-area`.
- Added `/validation/gemma-control`: captures an external before-frame, asks Gemma for a safe visible command, executes through `vector-sdk`, captures after-frame, and validates using robot telemetry plus external image delta.
- Real validation passed for a head-motion command: Gemma planned `head -> 35`, SDK executed, external camera saw Pip/cube/dock, telemetry changed, and image delta confirmed physical change. The SDK still reports `TimeoutError()` on some head futures even when motion succeeds; treat this as a warning to monitor.
- Planner timeout increased and Ollama `keep_alive` added to survive cold loading after vision models. External camera validation now prefers `llava:7b` over `moondream` and uses a prompt that detects Pip when partially visible at the frame edge.
- Research direction: keep following a SayCan-like architecture for Pip: Gemma proposes high-level actions, local affordance/safety scoring decides what is feasible, and external camera/robot telemetry closes the loop. RT-2/VLA work is inspiration for the long-term shape, but the practical next step is not end-to-end policy training; it is accumulating validated perception-action episodes and scoring action success.

### 2026-05-15 session 4 (Codex)
- Fixed the natural conversation loop after Pip speaks: ConversationSession now starts the listener when a wake word opens a session, drains stale transcripts, applies an echo guard while Pip speaks, then emits `conversation_reply_window_open` when the mic is live for Rob's follow-up.
- Added external microphone capture through ffmpeg/DirectShow. Set `VECTOR_AUDIO_INPUT_DEVICE="Microphone (Logi C615 HD WebCam)"` or pass `-AudioInputDevice` to `scripts/run-brain-host.ps1`. This bypasses Vector's audio feed when it reports repeated/static frames.
- Added expected-action validation so movement success requires telemetry to move toward the planned target, not merely "something changed." `/validation/control-suite` runs head/lift checks, with optional turn/drive checks.
- Added `environment_map.py`, `/map/status`, and `/map/observe` to persist pose + external camera descriptions as a first mapping journal. This is not SLAM yet; it is the durable episode layer needed before robust spatial autonomy.
- Current live run uses external Logi mic, voice bridge connected, listener enabled, autonomy/sentinel/vision running in real mode, and drive/turn autonomy disabled unless explicitly allowed.
