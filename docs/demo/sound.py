"""The demo video's sound track, built in code and laid onto the joined video.

    python sound.py DIR VIDEO OUT

Everything is synthesised here, so there is nothing to license: a soft click for every
typed character and a heavier one for Enter, a whoosh on each transition, a chime on the
title and end cards, and a quiet chord pad underneath that dips while the narrator
speaks. The narration WAVs come from `scenes.py voice`, and DIR/timeline.json (written by
`scenes.py assemble`) says where each part starts. Needs numpy and ffmpeg.
"""

from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from scenes import NARRATION, VOICE_AT

RATE = 44_100
rng = np.random.default_rng(7)  # the same "random" clicks on every build


def at(track: np.ndarray, second: float, sound: np.ndarray, gain: float = 1.0) -> None:
    start = int(second * RATE)
    if start >= len(track):
        return
    end = min(start + len(sound), len(track))
    track[start:end] += sound[: end - start] * gain


def envelope(n: int, tau: float) -> np.ndarray:
    return np.exp(-np.arange(n) / (tau * RATE))


def lowpass(signal: np.ndarray, width: int) -> np.ndarray:
    return np.convolve(signal, np.ones(width) / width, mode="same")


def band(n: int, low: int, high: int) -> np.ndarray:
    """Noise between two smoothing widths: a rough band-pass."""
    noise = rng.standard_normal(n)
    return lowpass(noise, high) - lowpass(noise, low)


def key_press(depth: float = 1.0) -> np.ndarray:
    """A key on a real keyboard: a tick as it bottoms out, a short hollow body, and the
    quieter key-up a moment later. depth > 1 is a bigger key (space, Enter)."""
    n = int(0.16 * RATE)
    t = np.arange(n) / RATE
    tick = band(n, 2, 6) * envelope(n, 0.0015)
    body = band(n, int(6 * depth), int(30 * depth)) * 2.5 * envelope(n, 0.012 * depth)
    ring = np.sin(2 * np.pi * rng.uniform(260, 420) / depth * t) * envelope(n, 0.008 * depth)
    down = tick + body + 0.3 * ring
    up = np.zeros(n)
    lag = int(rng.uniform(0.06, 0.1) * RATE)
    up[lag:] = (0.35 * band(n - lag, 2, 8) + 0.6 * band(n - lag, 8, 25)) * envelope(n - lag, 0.005)
    return (down + up) * rng.uniform(0.75, 1.0)


def typing_rhythm(start: float, end: float) -> list[tuple[float, float]]:
    """(second, depth) for each key of a person typing at ~9 keys a second: uneven gaps,
    a space bar every few keys. Not one key per character on screen, which types far
    faster than a person; this only has to sound like typing."""
    keys, now, since_space = [], start, 0
    while now < end:
        space = since_space >= rng.integers(4, 8)
        keys.append((now, 1.8 if space else 1.0))
        since_space = 0 if space else since_space + 1
        now += rng.gamma(5, 0.11 / 5) + (0.05 if space else 0)
    return keys


def whoosh(length: float = 0.55) -> np.ndarray:
    n = int(length * RATE)
    noise = rng.standard_normal(n)
    # Three smoothing widths blended over time: the sound brightens, then darkens.
    p = np.linspace(0, 1, n)
    soft, mid, bright = (lowpass(noise, w) for w in (60, 18, 5))
    swept = np.where(p < 0.5, soft + (mid - soft) * p * 2, mid + (bright - mid) * (p - 0.5) * 2)
    return swept * np.sin(np.pi * p) ** 2


def chime() -> np.ndarray:
    n = int(1.6 * RATE)
    t = np.arange(n) / RATE
    tone = sum(a * np.sin(2 * np.pi * f * t) for f, a in ((880, 1.0), (1318.5, 0.5), (1760, 0.25)))
    return tone * envelope(n, 0.45) * np.minimum(t / 0.005, 1)


def pad(length: float) -> np.ndarray:
    """Am - F - C - G, four seconds a chord, soft attacks and releases."""
    chords = [
        (220.0, 261.6, 329.6),
        (174.6, 220.0, 261.6),
        (261.6, 329.6, 392.0),
        (196.0, 246.9, 293.7),
    ]
    n = int(length * RATE)
    out = np.zeros(n)
    step = 4.0
    for i in range(int(length / step) + 1):
        notes = chords[i % len(chords)]
        size = int((step + 1.5) * RATE)
        t = np.arange(size) / RATE
        shape = np.minimum(t / 1.2, 1) * np.minimum((size / RATE - t) / 1.5, 1)
        tremolo = 1 + 0.08 * np.sin(2 * np.pi * 0.25 * t)
        tone = sum(np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t) for f in notes)
        tone += 0.8 * np.sin(np.pi * notes[0] * t)  # the root an octave down
        at(out, i * step, tone * shape * tremolo)
    fade = np.minimum(np.arange(n) / (2 * RATE), 1) * np.minimum((n - np.arange(n)) / (2 * RATE), 1)
    return out * fade


def read_voice(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        rate = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16) / 32768.0
    times = np.arange(int(len(data) * RATE / rate)) / RATE
    return np.interp(times, np.arange(len(data)) / rate, data)


def build(folder: Path) -> np.ndarray:
    timeline = json.loads((folder / "timeline.json").read_text())
    starts, length = timeline["starts"], timeline["length"]
    n = int(length * RATE) + RATE
    effects, voice = np.zeros(n), np.zeros(n)

    for name, (lead, typing, chars, output_at) in timeline["typing"].items():
        begin = starts[name]
        typed_until = begin + lead + chars * typing
        for second, depth in typing_rhythm(begin + lead, typed_until - 0.05):
            at(effects, second, key_press(depth), 0.025)
        at(effects, begin + output_at - 0.12, key_press(2.2), 0.035)

    for name, begin in starts.items():
        if name != "0-title":
            at(effects, begin - 0.1, whoosh(), 0.12)
    at(effects, 0.15, chime(), 0.10)
    at(effects, starts["9-end"] + 0.15, chime(), 0.10)

    for name in NARRATION:
        at(voice, starts[name] + VOICE_AT, read_voice(folder / f"voice-{name}.wav"), 0.9)

    # The pad dips to a third while someone speaks, rising back between lines.
    speaking = lowpass((np.abs(voice) > 0.01).astype(float), int(0.25 * RATE)) > 0.02
    duck = 1 - 0.65 * lowpass(speaking.astype(float), int(0.3 * RATE))
    music = pad(n / RATE) * 0.035 * duck
    mix = voice + effects + music
    return np.tanh(mix * 1.1) / np.tanh(1.1)  # gentle limiter


def write_wav(path: Path, samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


if __name__ == "__main__":
    folder, video, dst = (Path(a).expanduser() for a in sys.argv[1:4])
    track = folder / "sound.wav"
    write_wav(track, build(folder))
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(video),
            "-i",
            str(track),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar",
            "44100",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(dst),
        ],
        check=True,
    )
