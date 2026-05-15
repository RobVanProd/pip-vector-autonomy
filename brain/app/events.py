from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from itertools import count
from typing import Any

EVENTS: deque[dict[str, Any]] = deque(maxlen=200)
_IDS = count(1)


def add_event(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    event = {
        "id": next(_IDS),
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "payload": payload,
    }
    EVENTS.appendleft(event)
    return event


def list_events(limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    return list(EVENTS)[:limit]
