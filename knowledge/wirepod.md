# WirePod Notes

## What it is

wire-pod is community server software for Anki/Digital Dream Labs Vector. It replaces cloud voice/server functionality locally and can authenticate production Vector robots after escape-pod-capable firmware is installed.

Useful capabilities from docs:
- Local voice command server.
- Web app replacement for Vector mobile app.
- Custom commands/plugins.
- Knowledge graph / intent graph integrations, including Ollama-style local LLM possibilities depending on configuration path.
- Writes/modifies SDK config so Python SDK can keep working after wire-pod auth.

## Unlock / firmware path

Froggitti unlock page flow:
1. Keep Vector on charger.
2. Put Vector in recovery mode by holding back button ~15 seconds until rear lights are dark blue / recovery screen.
3. Use Chromium browser with Bluetooth support at `websetup.froggitti.net`.
4. Select Utility stack.
5. Pair with Vector; keep auto flow setup checked.
6. Connect Wi-Fi and select `Unlock-Prod.ota`.
7. Wait for reboot (~7 minutes).

Wire-pod wiki emphasizes production bots need special escape-pod firmware string ending in `ep`; plain 2.0.x is not enough.

## Developer hooks

wire-pod supports Go plugins:
- Define `Utterances`, `Name`, and `Action(transcribedText, botSerial, guid, target)`.
- Plugin can return a built-in Vector intent, custom spoken text, or pass through.
- `Utterances = []string{"*"}` can route every command through a plugin.
- For SDK control in Go plugins, docs mention `github.com/fforchino/vector-go-sdk` using vector/vectorpb packages.

There is also a wire-pod-compatible Python SDK fork:
- `https://github.com/kercre123/wirepod-vector-python-sdk`

## Open questions for tomorrow

- Best install mode on Rob's Windows machine: Windows prebuilt app vs Docker vs Linux/WSL path.
- Whether Docker networking/mDNS works cleanly enough for `escapepod.local` on Windows.
- Whether we should let wire-pod handle voice and use a separate Python executor for robot movement.

Initial recommendation: use the most reliable WirePod install path for first boot/auth, then integrate local brain separately after SDK access is confirmed.
