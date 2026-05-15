# Voice / TTS Direction

Goal: intelligible robot voice with emotional chirps. Inspired by warm cinematic robots, but original — not a WALL-E clone and not using copied WALL-E audio.

## Voice Requirements

- Rob must be able to understand it clearly.
- Short phrases should sound charming, not uncanny.
- Use light mechanical processing: small pitch movement, subtle bitcrush/vocoder texture, tiny chirps.
- Separate speech from nonverbal robot sounds.

## Recommended Voice Stack Options

### Option 1 — Vector built-in voice first

Use Vector's native `say_text` for first working version. Pros:
- Simple.
- Already robot-like.
- Works through SDK.

Cons:
- Less unique.

### Option 2 — Local TTS + robot earcons

Generate local TTS audio on host, then play through available Vector/audio path if feasible or through nearby speaker.

Possible local TTS candidates to research:
- Piper TTS: lightweight local voices.
- Kokoro / other local neural TTS if installed later.
- OpenClaw `tts` tool for experiments, but robot runtime should be local-first.

Processing chain idea:
1. TTS voice: clear, youthful/neutral.
2. Add subtle formant/pitch modulation.
3. Add quiet chirp before/after key emotions.
4. Keep consonants clear.

### Option 3 — Hybrid

- Vector native voice for direct robot speech.
- Host speaker for richer personality lines.
- Vector motions/eyes/chirps synced with host audio.

## Sound Design

Original sound palette:
- `[chirp]`: short upward sine/triangle blip.
- `[soft beep]`: gentle 700-900Hz ping.
- `[happy trill]`: 3-5 quick ascending notes.
- `[thinking clicks]`: quiet tick-tick pattern.
- `[sad boop]`: descending two-note tone.

Do not use ripped WALL-E samples. Create original earcons with similar emotional readability.

## Implementation Idea

Add a `voice_mode` layer:
- `native`: send text to `robot.behavior.say_text`.
- `host_tts`: synthesize/play on host speaker.
- `hybrid`: Vector performs animation while host plays generated voice.

The action schema can later expand:
- `{ "type": "sound", "cue": "happy_trill" }`
- `{ "type": "say", "text": "...", "voice": "native|host_tts|hybrid" }`

## Target Voice Calibration Phrase

Rob provided this sentence as the statistically useful spoken sample / target-voice calibration phrase:

> The beige hue on the waters of the loch impressed all, including the French queen, before she heard that symphony again, just as young Arthur wanted.

Use this as the standard test line when comparing TTS voices or voice conversion settings for Pip/Vector. Goal: clear intelligibility first, then warm chirpy mechanical character. If Rob provides an audio file of this line, save it as the reference sample and do not publish/share it externally.


## Reference Sample Saved

- Path: ector/personality/voice-reference/pip-target-voice-calibration.mp3 
- Metadata: ector/personality/voice-reference/metadata.json 
- ffprobe: ector/personality/voice-reference/ffprobe.json 
- Format: MP3, mono, 44.1 kHz, ~15.88 seconds, 128 kbps audio stream.

Use as local-only reference for Pip voice experiments. Do not publish or share externally.


## Kyma-Inspired Voice FX Direction

Rob described the target as a real-time synthesizer style: expressive pitch/time/formant performance, granular delay/resynthesis, and a **harmonic noise aura** that balances organic human expression with digital artifacts. We can emulate this with our own DSP chain and flagged words/tags rather than copying any existing character audio.

Prototype created:
- ector/voice-lab/README.md — design notes and markup flags.
- ector/voice-lab/pip_voice_fx.py — original earcon generator + post-processing prototype.
- ector/voice-lab/earcons/*.wav — original chirp/beep/trill/click assets.
- ector/voice-lab/reference-pip-fx-demo.wav — processed demo using Rob's calibration sample.

Core idea: Pip text can include tags like [happy trill], <bend word=...>, <stretch word=...>, <aura amount=...>. The renderer maps those to pitch curves, time-stretching, formant-ish preservation, granular delay, and tuned noise halos.

