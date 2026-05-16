from __future__ import annotations

import asyncio
from typing import Any

import httpx


_OLLAMA_GENERATE_LOCK = asyncio.Lock()


async def ollama_generate(
    ollama_base_url: str,
    payload: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    """Serialize local Ollama generate calls so brain and vision models do not thrash each other."""
    async with _OLLAMA_GENERATE_LOCK:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{ollama_base_url.rstrip('/')}/api/generate", json=payload)
            response.raise_for_status()
            return response.json()
