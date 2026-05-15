from __future__ import annotations

import json
import time
from typing import Any

import httpx
from fastapi.responses import StreamingResponse

from .events import add_event
from .memory import memory_context, remember_turn


VOICE_SYSTEM = """You are Vector, Rob's small local robot personality.
You are embodied in an Anki Vector robot. Keep replies short enough to speak aloud.
Be warm, curious, specific, and lightly playful.
Do not repeat filler like "just thinking" unless it truly fits.
If Rob says something conversational, respond conversationally.
If Rob gives a normal Vector command like go home or find cube, do not fight it.
Never claim abilities you do not have.
"""


def _messages_to_prompt(messages: list[dict[str, Any]]) -> tuple[str, str]:
    user_text = ""
    lines = [VOICE_SYSTEM, "", memory_context(), ""]
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if role == "system":
            lines.append(f"Additional system note: {content}")
        elif role == "assistant":
            lines.append(f"Vector: {content}")
        else:
            user_text = str(content)
            lines.append(f"Rob: {content}")
    lines.append("Vector:")
    return "\n".join(lines), user_text


async def chat_completion(payload: dict[str, Any], ollama_base_url: str, default_model: str):
    messages = payload.get("messages") or []
    model = payload.get("model") or default_model
    prompt, user_text = _messages_to_prompt(messages)
    stream = bool(payload.get("stream"))
    options = {
        "temperature": payload.get("temperature", 0.65),
        "top_p": payload.get("top_p", 0.9),
        "num_predict": min(int(payload.get("max_tokens") or 160), 220),
    }

    if stream:
        return StreamingResponse(
            _stream_ollama(ollama_base_url, model, prompt, options, user_text),
            media_type="text/event-stream",
        )

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{ollama_base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "options": options},
        )
        response.raise_for_status()
    text = response.json().get("response", "").strip()
    remember_turn(user_text, text)
    add_event("voice_chat", {"user_text": user_text, "assistant_text": text, "model": model, "raw": text})
    now = int(time.time())
    return {
        "id": f"chatcmpl-vector-{now}",
        "object": "chat.completion",
        "created": now,
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
    }


async def _stream_ollama(
    ollama_base_url: str,
    model: str,
    prompt: str,
    options: dict[str, Any],
    user_text: str,
):
    now = int(time.time())
    full = []
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST",
            f"{ollama_base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": True, "options": options},
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                token = data.get("response", "")
                if token:
                    full.append(token)
                    chunk = {
                        "id": f"chatcmpl-vector-{now}",
                        "object": "chat.completion.chunk",
                        "created": now,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                if data.get("done"):
                    break
    text = "".join(full).strip()
    remember_turn(user_text, text)
    add_event("voice_chat", {"user_text": user_text, "assistant_text": text, "model": model, "raw": text})
    done = {
        "id": f"chatcmpl-vector-{now}",
        "object": "chat.completion.chunk",
        "created": now,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(done)}\n\n"
    yield "data: [DONE]\n\n"
