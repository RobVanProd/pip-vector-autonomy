from __future__ import annotations

import json
import os

import httpx

from .personality import CHARACTER, clean_say_text, is_forbidden_speech, time_context
from .safety import SAFE_ANIMATION_ALIASES, safety_filter
from .memory import memory_context
from .schemas import Action, ActionList, PlanRequest, PlanResponse, SayAction, StopAction

OLLAMA_PLAN_TIMEOUT_SECONDS = float(os.getenv("VECTOR_OLLAMA_PLAN_TIMEOUT_SECONDS", "180"))
OLLAMA_REPLY_TIMEOUT_SECONDS = float(os.getenv("VECTOR_OLLAMA_REPLY_TIMEOUT_SECONDS", "120"))
OLLAMA_KEEP_ALIVE = os.getenv("VECTOR_OLLAMA_KEEP_ALIVE", "15m")


# ── SYSTEM PROMPT ──────────────────────────────────────────────────────────────
# Built once at import time from the CHARACTER block + JSON format rules.
# Injected into every /plan and /chat call so Gemma always knows who it is.

SYSTEM = (
    CHARACTER
    + """
OUTPUT FORMAT — return ONLY valid JSON, no markdown, no commentary:
Schema: {"actions": [ ... ]}

Allowed action objects:
- {"type":"say","text":"short phrase"}
- {"type":"drive","speed_mmps":-80..80,"duration_ms":100..2000}
- {"type":"turn","degrees":-90..90}
- {"type":"head","angle_deg":-20..40}
- {"type":"lift","height":"low"|"medium"|"high"}
- {"type":"animation","name":"happy"|"veryHappy"|"sad"|"confused"|"thinking"|"celebrate"|"love"}
- {"type":"behavior","name":"look_around"|"find_faces"|"connect_cube"|"roll_visible_cube"|"go_home"|"drive_off_charger"}
- {"type":"stop"}

Planning rules:
- Keep plans to 3-5 actions maximum.
- Always end with {"type":"stop"}.
- Use only the animation names listed above — no others.
- Use drive/turn ONLY when robot_state says connected=true and on_charger=false and no safety flags.
- For behaviors: look_around and find_faces use motion — only when safe.
- If Rob gives an explicit robot command, plan the matching robot action instead of only talking:
  "drive forward" -> drive; "drive backward" -> drive negative; "turn left/right" -> turn;
  "look around" -> behavior look_around; "find face" -> behavior find_faces;
  "find cube" or "connect cube" -> behavior connect_cube; "go home" -> behavior go_home.
- Prefer say + head tilt, say + animation, or pure animation + stop for expressive ticks.
- For normal conversation, answer Rob directly. You may use general knowledge, memory, and imagination.
- If Rob asks your favorite, preference, or opinion, choose a concrete in-character answer.
- Robot vision is sensory context only. It does not limit what you can discuss.
- If Rob asks to name, brainstorm, imagine, explain, or answer a fact, do that instead of reporting the desk.
- If Rob asks you to name something, include at least one concrete name in the say text.
- Never answer "I only see this desk" or "I only know this desk" unless Rob specifically asks what you see.
- Never use setup lines like "local knowledge check"; give the actual answer.
- If uncertain about safety or content, return {"actions":[{"type":"stop"}]}.

Examples:
{"actions":[{"type":"animation","name":"thinking"},{"type":"head","angle_deg":8},{"type":"stop"}]}
{"actions":[{"type":"say","text":"Rob, battery looks okay. Ready when you are."},{"type":"animation","name":"happy"},{"type":"stop"}]}
{"actions":[{"type":"say","text":"Something moved. I am looking."},{"type":"behavior","name":"look_around"},{"type":"stop"}]}
{"actions":[{"type":"head","angle_deg":14},{"type":"animation","name":"curious"},{"type":"stop"}]}
"""
)

# ── SPEECH QUALITY GUARD ───────────────────────────────────────────────────────
# Any say action whose text matches these patterns is dropped.
# Kept in sync with personality.py _FORBIDDEN_SUBSTRINGS for belt-and-suspenders coverage.
LOW_VALUE_SPEECH_PATTERNS = (
    "i had a thought",
    "i had an idea",
    "kept it safe",
    "keeping it safe",
    "followed the rules",
    "just thinking",
    "pondering life",
    "ummm",
    "beep boop",
    "boop beep",
    "nothing to say",
    "let me think",
    "i will now",
    "as pip",
    "as vector",
    "action plan",
    "json",
    "i cannot say",
    "i am unable",
    "sorry, i",
    "local knowledge check",
    "knowledge check",
    "i know a fact",
    "don't have a favorite",
    "do not have a favorite",
    "i only see this desk",
    "i only know this desk",
    "only see this desk",
    "only know this desk",
    "i just see this desk",
    "i just know this desk",
)


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found")
    return json.loads(text[start : end + 1])


def validate_actions(data: dict) -> list[Action]:
    actions = data.get("actions", [])
    if not isinstance(actions, list):
        raise ValueError("actions must be a list")
    return ActionList.validate_python([repair_action(action) for action in actions[:5]])


def repair_action(action: object) -> object:
    if not isinstance(action, dict):
        return action
    action_type = action.get("type")
    # Handle case where Gemma uses animation name directly as type
    if action_type in SAFE_ANIMATION_ALIASES:
        return {"type": "animation", "name": action_type}
    # Handle case where Gemma says "curious" which isn't a valid alias — map to "thinking"
    if action_type == "animation":
        name = action.get("name", "")
        if name == "curious":
            return {**action, "name": "thinking"}
        if name == "excited":
            return {**action, "name": "veryHappy"}
        if name == "angry":
            return {**action, "name": "sad"}
    return action


async def create_plan(
    req: PlanRequest,
    *,
    model: str,
    ollama_base_url: str,
    execution_mode: str,
) -> PlanResponse:
    prompt = (
        f"{time_context()}\n"
        f"Memory and context:\n{memory_context()}\n\n"
        f"Robot state JSON: {req.robot_state.model_dump_json(exclude_none=True)}\n"
        f"Input: {req.user_text}\n"
        "Return JSON plan now."
    )
    payload = {
        "model": model,
        "prompt": f"{SYSTEM}\n\n{prompt}",
        "format": "json",
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {"temperature": 0.38, "num_predict": 280},
    }
    async with httpx.AsyncClient(timeout=OLLAMA_PLAN_TIMEOUT_SECONDS) as client:
        response = await client.post(f"{ollama_base_url}/api/generate", json=payload)
        response.raise_for_status()

    raw = response.json().get("response", "")
    parse_error = None
    try:
        parsed = extract_json(raw)
        proposed = validate_actions(parsed)
    except Exception as exc:
        parse_error = str(exc)
        proposed = [StopAction(type="stop")]

    proposed = _clean_say_actions(proposed)
    proposed, speech_notes = filter_low_value_speech(proposed)
    actions, denied, notes = safety_filter(proposed, req.robot_state)
    if parse_error:
        notes.append(f"planner JSON parse failed; stopped silently: {parse_error}")
    notes.extend(speech_notes)
    return PlanResponse(
        model=model,
        actions=actions,
        denied_actions=denied,
        safety_notes=notes,
        raw=raw,
        execution_mode=execution_mode,
    )


async def create_conversation_plan(
    req: PlanRequest,
    *,
    model: str,
    ollama_base_url: str,
    execution_mode: str,
) -> PlanResponse:
    try:
        plan = await create_plan(
            req,
            model=model,
            ollama_base_url=ollama_base_url,
            execution_mode=execution_mode,
        )
    except Exception as exc:
        return await _fallback_conversation_plan(
            req,
            model=model,
            ollama_base_url=ollama_base_url,
            execution_mode=execution_mode,
            denied_actions=[],
            notes=[f"planner request failed; used conversational fallback: {type(exc).__name__}: {exc}"],
            raw="",
        )
    if any(action.type == "say" for action in plan.actions) and not _conversation_needs_fallback(req.user_text, plan.actions):
        return plan

    return await _fallback_conversation_plan(
        req,
        model=model,
        ollama_base_url=ollama_base_url,
        execution_mode=execution_mode,
        denied_actions=plan.denied_actions,
        notes=[*plan.safety_notes, "used conversational fallback because action planner produced no speech"],
        raw=plan.raw,
    )


async def _fallback_conversation_plan(
    req: PlanRequest,
    *,
    model: str,
    ollama_base_url: str,
    execution_mode: str,
    denied_actions: list[dict],
    notes: list[str],
    raw: str,
) -> PlanResponse:
    reply = await create_reply_text(req, model=model, ollama_base_url=ollama_base_url)
    proposed: list[Action] = [SayAction(type="say", text=reply), StopAction(type="stop")]
    proposed = _clean_say_actions(proposed)
    proposed, speech_notes = filter_low_value_speech(proposed)
    actions, denied, safety_notes = safety_filter(proposed, req.robot_state)
    safety_notes.extend(notes)
    safety_notes.extend(speech_notes)
    return PlanResponse(
        model=model,
        actions=actions,
        denied_actions=[*denied_actions, *denied],
        safety_notes=safety_notes,
        raw=raw,
        execution_mode=execution_mode,
    )


async def create_reply_text(req: PlanRequest, *, model: str, ollama_base_url: str) -> str:
    prompt = (
        f"{CHARACTER}\n\n"
        "Answer Rob in one short concrete phrase — 3 to 12 words. "
        "No JSON. No safety meta. No filler. No stage directions like [chirp].\n"
        "Use general knowledge and imagination when Rob asks normal questions. "
        "If Rob asks a fact, answer the fact immediately. "
        "Robot vision is only sensory context; never refuse because something is not visible.\n\n"
        f"{time_context()}\n"
        f"Memory:\n{memory_context(max_turns=5, max_facts=12)}\n\n"
        f"Robot state: {req.robot_state.model_dump_json(exclude_none=True)}\n"
        f"Rob said: {req.user_text}\n"
        "Pip replies:"
    )
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {"temperature": 0.55, "num_predict": 80},
    }
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_REPLY_TIMEOUT_SECONDS) as client:
            response = await client.post(f"{ollama_base_url}/api/generate", json=payload)
            response.raise_for_status()
        text = (response.json().get("response") or "").strip()
    except Exception:
        text = ""
    text = _clean_reply_text(text)
    if text:
        return text[:180]
    text = await _create_plain_reply_text(req, model=model, ollama_base_url=ollama_base_url)
    if text:
        return text[:180]
    return _local_reply_fallback(req.user_text)


# ── INTERNAL HELPERS ───────────────────────────────────────────────────────────

def _clean_say_actions(actions: list[Action]) -> list[Action]:
    """
    Strip bracketed stage cues from say action text before the plan is evaluated.
    This is a belt-and-suspenders complement to clean_say_text() in executors.py.
    Example: say("[chirp] Hello Rob") → say("Hello Rob")
    """
    result: list[Action] = []
    for action in actions:
        if action.type == "say":
            cleaned = clean_say_text(action.text)
            if cleaned:
                result.append(SayAction(type="say", text=cleaned[:180]))
            # else: drop the action — empty say after cleaning is useless
        else:
            result.append(action)
    return result


def filter_low_value_speech(
    actions: list[Action],
) -> tuple[list[Action], list[str]]:
    """
    Remove say actions whose text matches LOW_VALUE_SPEECH_PATTERNS.
    Returns (filtered_actions, notes) where notes documents what was dropped.
    """
    filtered: list[Action] = []
    notes: list[str] = []
    for action in actions:
        if action.type == "say" and is_forbidden_speech(action.text):
            notes.append(f"dropped low-value speech: {action.text!r}")
        else:
            filtered.append(action)
    return filtered, notes


def _conversation_needs_fallback(user_text: str, actions: list[Action]) -> bool:
    """
    Return True if the action plan doesn't adequately address a conversational request.
    When True, create_conversation_plan falls back to create_reply_text.

    A plan needs fallback when:
    - Rob asked a question (ends with ?) but Gemma returned no say action
    - The only say action is very short (≤ 3 chars) — probably garbled
    """
    has_say = any(a.type == "say" for a in actions)
    if not has_say:
        return True
    # If Rob asked a question, ensure the answer is substantive
    if user_text.strip().endswith("?"):
        say_texts = [a.text for a in actions if a.type == "say"]
        if all(len(t.strip()) <= 3 for t in say_texts):
            return True
    return False


def _clean_reply_text(text: str) -> str:
    """
    Clean raw Gemma reply text for use as TTS say text.
    - Strip stage cues
    - Remove leading labels like "Pip:" or "Assistant:"
    - Remove JSON fragments
    - Enforce length limits
    """
    if not text:
        return ""
    # Strip stage cues
    text = clean_say_text(text)
    # Remove common Gemma label prefixes
    for prefix in ("Pip:", "pip:", "Assistant:", "assistant:", "Vector:", "vector:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    # Drop if it looks like JSON leaked through
    if text.startswith("{") or text.startswith("["):
        return ""
    # Drop if it's forbidden speech
    if is_forbidden_speech(text):
        return ""
    return text.strip()


async def _create_plain_reply_text(
    req: PlanRequest,
    *,
    model: str,
    ollama_base_url: str,
) -> str:
    """
    Ultra-minimal fallback: ask Gemma for a single plain text line with no JSON.
    Called when create_reply_text's primary path returns empty.
    """
    prompt = (
        f"You are Pip, a small robot. Rob said: {req.user_text!r}\n"
        "Reply in one short phrase (3-10 words). No JSON. No stage directions. "
        "No filler. Be concrete and in character."
    )
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {"temperature": 0.5, "num_predict": 40},
    }
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_REPLY_TIMEOUT_SECONDS) as client:
            response = await client.post(f"{ollama_base_url}/api/generate", json=payload)
            response.raise_for_status()
        text = (response.json().get("response") or "").strip()
        return _clean_reply_text(text)
    except Exception:
        return ""


def _local_reply_fallback(user_text: str) -> str:
    """
    Pure local fallback — no LLM call. Returns a safe, in-character phrase.
    Used only when all Gemma paths fail (network down, timeout, etc.).
    """
    import random
    phrases = [
        "Pip here. Say again?",
        "Local brain is alive. Repeat that?",
        "Hmm. Still processing, Rob.",
        "Running locally. Come again?",
        "Tiny brain is thinking, Rob.",
    ]
    return random.choice(phrases)
