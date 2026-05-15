from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MEMORY_PATH = Path(os.getenv("VECTOR_MEMORY_PATH", "memory.json"))
_AUTONOMOUS_SAY_WINDOW = 8   # how many recent idle phrases to keep for repetition guard

DEFAULT_MEMORY: dict[str, Any] = {
    "facts": [
        "The human is Rob.",
        "The robot's wake word/name is Vector.",
        "Vector is a small local robot running with Gemma as the local brain.",
        "Rob likes fast, real builds with bounded and logged external actions.",
    ],
    "recent_turns": [],
}


def load_memory() -> dict[str, Any]:
    if not MEMORY_PATH.exists():
        save_memory(DEFAULT_MEMORY)
        return DEFAULT_MEMORY.copy()
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = DEFAULT_MEMORY.copy()
    data.setdefault("facts", [])
    data.setdefault("recent_turns", [])
    return data


def save_memory(data: dict[str, Any]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def memory_context(max_turns: int = 8, max_facts: int = 30) -> str:
    data = load_memory()
    facts = "\n".join(f"- {fact}" for fact in data.get("facts", [])[:max_facts])
    turns = "\n".join(
        f"- Rob: {turn.get('user', '')}\n  Vector: {turn.get('assistant', '')}"
        for turn in data.get("recent_turns", [])[-max_turns:]
    )
    return f"Known facts:\n{facts or '- none'}\n\nRecent conversation:\n{turns or '- none'}"


def remember_turn(user_text: str, assistant_text: str) -> None:
    data = load_memory()
    data["recent_turns"].append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "user": user_text[:500],
            "assistant": assistant_text[:500],
        }
    )
    data["recent_turns"] = data["recent_turns"][-40:]
    _extract_facts(data, user_text)
    save_memory(data)


def remember_autonomous_say(tick: int, say_text: str, actions_summary: str) -> None:
    """
    Store what Pip said during an autonomous idle tick.
    This feeds the repetition-guard in the next idle prompt.
    It is stored separately from recent_turns so it does not pollute
    the interactive conversation history.
    """
    data = load_memory()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tick": tick,
        "say": say_text[:200],
        "actions": actions_summary[:200],
    }
    log: list[dict[str, Any]] = data.setdefault("autonomous_ticks", [])
    log.append(entry)
    data["autonomous_ticks"] = log[-_AUTONOMOUS_SAY_WINDOW:]
    save_memory(data)


def recent_autonomous_says(n: int = 4) -> list[str]:
    """
    Return the last n phrases Pip said autonomously.
    Used to build a 'do not repeat these' block for the idle prompt.
    """
    data = load_memory()
    ticks: list[dict[str, Any]] = data.get("autonomous_ticks", [])
    return [t["say"] for t in ticks[-n:] if t.get("say")]


def add_fact(fact: str) -> dict[str, Any]:
    data = load_memory()
    clean = fact.strip()
    if clean and clean not in data["facts"]:
        data["facts"].append(clean[:300])
    save_memory(data)
    return data


def _extract_facts(data: dict[str, Any], user_text: str) -> None:
    text = user_text.strip()
    patterns = [
        r"\bmy name is ([A-Za-z][A-Za-z0-9 _'-]{1,40})",
        r"\bi am ([A-Za-z][A-Za-z0-9 _'-]{1,40})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            name = match.group(1).strip(" .,!?:;")
            fact = f"The human's name is {name}."
            if fact not in data["facts"]:
                data["facts"].append(fact)

    remember_match = re.search(r"\bremember(?: that)? (.+)", text, flags=re.IGNORECASE)
    if remember_match:
        fact = remember_match.group(1).strip(" .")
        if fact:
            clean = fact[0].upper() + fact[1:]
            if clean not in data["facts"]:
                data["facts"].append(clean[:300])
