# Vector Knowledge Base

This folder is the working knowledge base for Rob's Anki Vector local-AI project.

## North Star

Make Vector feel alive locally: voice/server handled through wire-pod, personality/planning through local Gemma, and physical actions guarded by a deterministic safety layer.

## Current machine prep

- Docker Desktop installed and daemon verified.
- Ollama installed and serving at `127.0.0.1:11434`.
- Local models already present:
  - `gemma4:e4b`
  - `hf.co/unsloth/gemma-4-E4B-it-GGUF:Q4_K_M`
  - `llava:7b`
  - `llava:13b`
- `vector-brain` Docker image builds successfully.

## Main components

- `README.md` — overall setup/research notes.
- `TOMORROW.md` — physical-arrival quickstart.
- `brain/` — local FastAPI safety/planning service.
- `knowledge/` — deeper notes on SDK, wire-pod, controller/teleop, and policy-training ideas.

## Design Principle

LLM proposes. Safety layer disposes.

Gemma can propose a short JSON plan, but only the validator/executor can issue robot commands. This keeps movement bounded and lets us log everything for later learning.
