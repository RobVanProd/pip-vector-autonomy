# Pip Voice Lab

Goal: build an original Kyma-inspired robot voice texture for Pip/Vector: intelligible speech plus expressive digital artifacts, harmonic noise aura, and charming beeps/boops.

Important: this is **inspired by a sound-design method**, not a WALL-E clone. Do not use ripped WALL-E samples or impersonate the character. Pip's voice should be original.

## Design Target

Rob's phrase/reference sample:

> The beige hue on the waters of the loch impressed all, including the French queen, before she heard that symphony again, just as young Arthur wanted.

Reference file:

`../personality/voice-reference/pip-target-voice-calibration.mp3`

## Concept

Pipeline:

1. Base speech: clear TTS or Vector native speech.
2. Prosody gestures: flagged words/syllables can be pitch-bent or time-stretched.
3. Formant-preserving-ish layer: keep a small/childlike throat impression while bending pitch.
4. Granular delay/resynthesis: tiny grains echo around consonants/vowels.
5. Harmonic noise aura: quiet tuned noise/partials around the voice, gated by speech energy.
6. Earcons: original chirps, trills, boops, thinking clicks.
7. Safety/intelligibility limiter: speech remains understandable.

## Markup / Key Flags

Pip text can contain expressive tags that the voice renderer interprets:

- `[chirp]` — short upward blip.
- `[soft beep]` — single gentle note.
- `[happy trill]` — fast ascending notes.
- `[thinking clicks]` — quiet rhythmic ticks.
- `[sad boop]` — descending two-note cue.
- `<bend word="hello" cents="180" ms="250">` — pitch gesture on a word.
- `<stretch word="tiny" factor="1.4">` — syllable/word elongation.
- `<aura amount="0.25">` — add harmonic noise halo.
- `<robotize amount="0.15">` — subtle digital texture.

Initial runtime can ignore advanced XML-like tags until we have a better renderer. Earcon tags are easiest first.

## Controller-like Vocal Performance Idea

The Kyma/tablet idea maps beautifully to a live performance interface:

- X axis: pitch bend / vowel curve.
- Y axis: formant size / throat impression.
- Pressure: grain density / aura intensity.
- Pen speed: delay smear.

We can replicate this digitally with automation curves rather than a physical tablet at first:

```json
{
  "word": "Rob",
  "pitch_curve_cents": [[0,0],[0.35,160],[1.0,40]],
  "formant_curve": [[0,0.92],[1,0.88]],
  "grain_density": 0.22,
  "aura": 0.18
}
```

## Modes

### v0: Earcon Layer

Generate original beeps/trills and play them around native speech.

### v1: Post-process TTS

Take a TTS WAV/MP3 and apply:
- subtle chorus
- granular delay
- harmonic aura
- tiny bitcrush/saturation

### v2: Word-flagged Expressive Renderer

Parse Pip speech with tags and apply gestures to specific words/syllables.

### v3: Live Voice Instrument

Optional UI/tablet/controller where Rob can "perform" vowel/pitch curves and save presets.

## Intelligibility Rule

If the voice sounds cool but Rob cannot understand it, it fails. Keep artifacts low by default and make the weirdness an adjustable style parameter.
