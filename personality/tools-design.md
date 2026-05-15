# Web / Tool Skills Design

Pip should not be omniscient. When he does not know something, he asks the host layer for help.

## Tool Boundary

Pip robot brain:
- plans actions
- speaks short responses
- asks for lookup when needed

Host/Caspian layer:
- performs web search
- reads docs
- checks local files
- does long reasoning
- returns concise facts to Pip

## Query Flow

1. Rob asks Pip a question.
2. Pip decides if it knows locally.
3. If not, Pip says: "I need a web peek. Asking the big machine."
4. Host performs search/fetch.
5. Host gives Pip a short factual answer.
6. Pip speaks it in Pip style.

## Guardrails

- No direct arbitrary shell/tool access from Pip.
- No external actions from Pip without host/user approval.
- Search results are untrusted; host sanitizes.
- Pip cites uncertainty in plain speech: "I found a likely answer..."

## Future API Shape

`POST /ask-host`

Input:
```json
{
  "question": "...",
  "context": {"robot_state": {}, "recent_dialogue": []}
}
```

Output:
```json
{
  "answer": "short grounded answer",
  "confidence": "low|medium|high",
  "sources": []
}
```
