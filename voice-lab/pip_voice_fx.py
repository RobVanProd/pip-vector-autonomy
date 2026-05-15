"""Original Pip robot voice FX prototype.

This is a lightweight Kyma-inspired post-processor / earcon generator.
It does not clone or use any copyrighted robot samples.

Usage:
  python pip_voice_fx.py earcons
  python pip_voice_fx.py process input.wav output.wav --aura 0.18 --grain 0.12 --robot 0.08

Input should be mono/stereo WAV. Use ffmpeg to convert MP3/TTS if needed.
"""

from __future__ import annotations

import argparse
import math
import wave
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, lfilter

SR = 44100


def ensure_float(x: np.ndarray) -> np.ndarray:
    if x.dtype == np.float32 or x.dtype == np.float64:
        y = x.astype(np.float32)
    elif x.dtype == np.int16:
        y = x.astype(np.float32) / 32768.0
    elif x.dtype == np.int32:
        y = x.astype(np.float32) / 2147483648.0
    else:
        y = x.astype(np.float32)
        m = np.max(np.abs(y)) or 1.0
        y = y / m
    if y.ndim == 2:
        y = y.mean(axis=1)
    return np.clip(y, -1, 1)


def write_wav(path: str | Path, sr: int, y: np.ndarray):
    y = np.nan_to_num(y)
    peak = np.max(np.abs(y)) or 1.0
    if peak > 0.98:
        y = y / peak * 0.98
    wavfile.write(str(path), sr, (y * 32767).astype(np.int16))


def env_adsr(n: int, attack=0.01, release=0.06, sr=SR):
    env = np.ones(n, dtype=np.float32)
    a = min(n, int(attack * sr))
    r = min(n, int(release * sr))
    if a > 0:
        env[:a] *= np.linspace(0, 1, a)
    if r > 0:
        env[-r:] *= np.linspace(1, 0, r)
    return env


def tone(freq: float, dur: float, sr=SR, amp=0.25, bend=0.0):
    n = int(dur * sr)
    t = np.arange(n) / sr
    if bend:
        f = freq * (1 + bend * np.linspace(0, 1, n))
        phase = 2 * np.pi * np.cumsum(f) / sr
    else:
        phase = 2 * np.pi * freq * t
    # Triangle-ish with a little sine for soft robotic character.
    y = 0.65 * np.sin(phase) + 0.25 * np.sin(2 * phase) + 0.10 * np.sin(3 * phase)
    return amp * y * env_adsr(n, sr=sr)


def silence(dur: float, sr=SR):
    return np.zeros(int(dur * sr), dtype=np.float32)


def happy_trill():
    parts = []
    for f in [660, 830, 990, 1320]:
        parts.append(tone(f, 0.075, amp=0.18, bend=0.08))
        parts.append(silence(0.018))
    return np.concatenate(parts)


def chirp():
    return tone(760, 0.16, amp=0.20, bend=0.65)


def soft_beep():
    return tone(840, 0.18, amp=0.16, bend=0.03)


def sad_boop():
    return np.concatenate([tone(620, 0.13, amp=0.16, bend=-0.08), silence(0.03), tone(410, 0.20, amp=0.14, bend=-0.04)])


def thinking_clicks():
    rng = np.random.default_rng(42)
    parts = []
    for _ in range(5):
        n = int(0.025 * SR)
        click = rng.normal(0, 0.10, n).astype(np.float32) * env_adsr(n, attack=0.001, release=0.02)
        parts.append(click)
        parts.append(silence(0.07))
    return np.concatenate(parts)


def lowpass(y: np.ndarray, cutoff: float, sr: int):
    b, a = butter(2, cutoff / (sr / 2), btype="low")
    return lfilter(b, a, y).astype(np.float32)


def highpass(y: np.ndarray, cutoff: float, sr: int):
    b, a = butter(1, cutoff / (sr / 2), btype="high")
    return lfilter(b, a, y).astype(np.float32)


def harmonic_noise_aura(y: np.ndarray, sr: int, amount: float):
    if amount <= 0:
        return y
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 1, len(y)).astype(np.float32)
    # Speech-energy envelope, smoothed.
    env = lowpass(np.abs(y), 18, sr)
    env = env / (np.max(env) or 1.0)
    # Tuned partial haze. Not voice cloning; just a quiet spectral halo.
    t = np.arange(len(y)) / sr
    partials = (
        0.45 * np.sin(2 * np.pi * 220 * t)
        + 0.25 * np.sin(2 * np.pi * 440 * t)
        + 0.18 * np.sin(2 * np.pi * 660 * t)
        + 0.12 * np.sin(2 * np.pi * 990 * t)
    ).astype(np.float32)
    haze = lowpass(noise, 2800, sr) * 0.35 + partials * 0.65
    return y + amount * env * haze * 0.18


def granular_delay(y: np.ndarray, sr: int, amount: float):
    if amount <= 0:
        return y
    out = y.copy()
    delays = [0.037, 0.061, 0.089]
    gains = [0.18, 0.12, 0.08]
    # Modulated tiny echoes create a resynthesis smear without destroying words.
    for d, g in zip(delays, gains):
        shift = int(d * sr)
        delayed = np.zeros_like(y)
        delayed[shift:] = y[:-shift]
        flutter = 1.0 + 0.35 * np.sin(2 * np.pi * np.arange(len(y)) / sr * (3.1 + d * 10))
        out += amount * g * delayed * flutter.astype(np.float32)
    return out


def subtle_robotize(y: np.ndarray, sr: int, amount: float):
    if amount <= 0:
        return y
    # Gentle ring modulation + saturation + tiny quantization blend.
    t = np.arange(len(y)) / sr
    ring = y * np.sin(2 * np.pi * 42 * t).astype(np.float32)
    sat = np.tanh(y * (1.0 + amount * 2.5))
    steps = 2 ** 10
    quant = np.round(y * steps) / steps
    return (1 - amount) * y + amount * (0.35 * ring + 0.45 * sat + 0.20 * quant)


def process_wav(inp: str, out: str, aura: float, grain: float, robot: float):
    sr, data = wavfile.read(inp)
    y = ensure_float(data)
    y = highpass(y, 60, sr)
    y = harmonic_noise_aura(y, sr, aura)
    y = granular_delay(y, sr, grain)
    y = subtle_robotize(y, sr, robot)
    write_wav(out, sr, y)


def make_earcons(outdir: str):
    p = Path(outdir)
    p.mkdir(parents=True, exist_ok=True)
    for name, fn in {
        "chirp": chirp,
        "soft_beep": soft_beep,
        "happy_trill": happy_trill,
        "sad_boop": sad_boop,
        "thinking_clicks": thinking_clicks,
    }.items():
        write_wav(p / f"{name}.wav", SR, fn())


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("earcons")
    e.add_argument("--outdir", default="earcons")
    p = sub.add_parser("process")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--aura", type=float, default=0.16)
    p.add_argument("--grain", type=float, default=0.10)
    p.add_argument("--robot", type=float, default=0.06)
    args = ap.parse_args()
    if args.cmd == "earcons":
        make_earcons(args.outdir)
    elif args.cmd == "process":
        process_wav(args.input, args.output, args.aura, args.grain, args.robot)


if __name__ == "__main__":
    main()
