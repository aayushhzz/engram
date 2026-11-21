"""Original soundtrack for the Engram video, synthesized from scratch (no samples,
no third-party audio). 120 BPM, 4/4, Am7 - F - C - G. Every event lands on the
beat grid, and each scene cut gets a riser and an impact.

Output: music.wav (30 s, 44.1 kHz, stereo, 16-bit).
"""
import math
import random
import struct
import wave
from pathlib import Path

SR = 44100
DUR = 30.0
N = int(SR * DUR)
BEAT = 0.5
L = [0.0] * N
R = [0.0] * N
rng = random.Random(42)
TAU = 2 * math.pi


def add(start, length, gen, gain=1.0, pan=0.0):
    """Mix a sound into the buffer. gen(t) returns a sample for local time t."""
    gl = gain * (1 - pan) * 0.5 + gain * 0.5
    gr = gain * (1 + pan) * 0.5 + gain * 0.5
    s0 = int(start * SR)
    for k in range(int(length * SR)):
        i = s0 + k
        if i < 0 or i >= N:
            continue
        v = gen(k / SR)
        L[i] += v * gl
        R[i] += v * gr


def kick(t):
    f = 45 + 75 * (1 - math.exp(-25 * t)) / 25
    phase = TAU * (45 * t + 75 * (1 - math.exp(-25 * t)) / 25)
    return math.sin(phase) * math.exp(-6 * t) * 0.9


def snare(t):
    return (rng.uniform(-1, 1) * 0.5 + math.sin(TAU * 180 * t) * 0.4) * math.exp(-22 * t) * 0.35


_hat_prev = [0.0]


def hat(t):
    x = rng.uniform(-1, 1)
    hp = x - _hat_prev[0]
    _hat_prev[0] = x
    return hp * math.exp(-90 * t) * 0.12


def bass_note(freq):
    def gen(t):
        env = min(1.0, t / 0.01) * math.exp(-4 * t)
        w = math.sin(TAU * freq * t) + 0.35 * math.sin(TAU * 2 * freq * t) + 0.12 * math.sin(TAU * 3 * freq * t)
        return w * env * 0.22
    return gen


def pad_voice(freq, length):
    def gen(t):
        a = min(1.0, t / 0.6)
        r = min(1.0, (length - t) / 0.8) if length - t < 0.8 else 1.0
        return (math.sin(TAU * freq * t) + 0.5 * math.sin(TAU * freq * 1.003 * t)) * a * r * 0.045
    return gen


def riser(length):
    y = [0.0]

    def gen(t):
        a = 0.004 + 0.27 * (t / length) ** 2
        x = rng.uniform(-1, 1)
        y[0] += a * (x - y[0])
        return y[0] * (t / length) ** 1.5 * 0.9
    return gen


def impact(t):
    boom = math.sin(TAU * (58 - 20 * (1 - math.exp(-8 * t))) * t) * math.exp(-3.2 * t)
    cym = rng.uniform(-1, 1) * math.exp(-2.6 * t) * 0.22
    return boom * 0.8 + cym


# Chords: (root bass freq, pad notes)
CHORDS = [
    (110.0, [220.0, 261.6, 329.6, 392.0]),  # Am7
    (87.3, [174.6, 220.0, 261.6]),          # F
    (130.8, [261.6, 329.6, 392.0]),         # C
    (98.0, [196.0, 246.9, 293.7]),          # G
]
BARS = int(DUR / (4 * BEAT))  # 15 bars of 2 s

# Pads and bass for the whole track
for bar in range(BARS):
    bass_f, pads = CHORDS[bar % 4]
    t0 = bar * 4 * BEAT
    for f in pads:
        add(t0, 4 * BEAT, pad_voice(f, 4 * BEAT), gain=1.0, pan=rng.uniform(-0.4, 0.4))
    for eighth in range(8):
        add(t0 + eighth * BEAT / 2, BEAT / 2, bass_note(bass_f if eighth % 4 != 2 else bass_f * 2), gain=0.9)

# Groove from 2.0 s (scene 2) to the end; drums drop out for the 22.0–22.5 s hit
GROOVE_START = 2.0
GROOVE_END = 29.5
beat = 0
t = GROOVE_START
while t < GROOVE_END:
    if not (21.9 < t < 22.6):  # breath before the outro hit
        if beat % 2 == 0:
            add(t, 0.4, kick, gain=0.95)
        else:
            add(t, 0.25, snare, gain=1.0, pan=0.05)
        add(t + BEAT / 2, 0.08, hat, gain=0.8, pan=-0.2)
        add(t, 0.08, hat, gain=0.6, pan=0.2)
    t += BEAT
    beat += 1

# Risers into each scene cut, plus an impact on the cut itself
CUTS = [2, 4.5, 10, 14, 18, 22, 26]
for c in CUTS:
    if c >= 2.5:
        add(c - 0.9, 0.9, riser(0.9), gain=0.55)
    add(c, 1.6, impact, gain=0.7 if c != 2 else 0.5)

# Final chord: long Am add9 bloom for the outro
for f in [110.0, 220.0, 261.6, 329.6, 493.9]:
    add(26.0, 4.0, pad_voice(f, 4.0), gain=1.2, pan=rng.uniform(-0.3, 0.3))
add(29.5, 0.7, impact, gain=0.5)

# Character sounds, synthesized here (not recorded or copied from any game):
# a villager-style "hmm" (low, nasal glide), a short trade chirp, and a pickaxe clank.
def hmm(t):
    env = min(1.0, t / 0.08) * math.exp(-3.0 * t)
    phase = TAU * (210 * t + 40 * (0.5 / math.pi) * (1 - math.cos(math.pi * t / 0.5)))
    wave = sum(math.sin(n * phase) / n for n in range(1, 7))
    return wave * env * 0.45


def chirp(t):
    return math.sin(TAU * 1100 * t + 600 * t * t) * math.exp(-28 * t)


def clank(t):
    return rng.uniform(-1, 1) * math.exp(-60 * t) * 0.25 + math.sin(TAU * 1800 * t) * math.exp(-25 * t) * 0.15


add(15.2, 0.5, hmm, gain=0.42, pan=-0.2)    # scribe answers
add(16.5, 0.45, hmm, gain=0.35, pan=-0.2)
add(22.6, 0.09, chirp, gain=0.3, pan=0.3)    # trade chirps
add(22.72, 0.09, chirp, gain=0.3, pan=0.3)
add(22.84, 0.09, chirp, gain=0.3, pan=0.3)
for k, t0 in enumerate([26.6, 26.75, 26.9]):  # outro burst
    add(t0, 0.09, chirp, gain=0.32, pan=0.4 - 0.4 * k)
add(2.6, 0.12, clank, gain=0.5, pan=0.5)     # miner's pickaxe hop

# Master: fade in/out, soft clip, normalize
FADE_IN = int(0.2 * SR)
FADE_OUT = int(1.2 * SR)
peak = 0.0
for i in range(N):
    g = 1.0
    if i < FADE_IN:
        g = i / FADE_IN
    if i > N - FADE_OUT:
        g = (N - i) / FADE_OUT
    L[i] = math.tanh(L[i] * g * 1.1)
    R[i] = math.tanh(R[i] * g * 1.1)
    peak = max(peak, abs(L[i]), abs(R[i]))
norm = 0.89 / peak if peak > 0 else 1.0

out = Path(__file__).with_name("music.wav")
with wave.open(str(out), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    frames = bytearray()
    for i in range(N):
        frames += struct.pack("<hh", int(L[i] * norm * 32767), int(R[i] * norm * 32767))
    w.writeframes(bytes(frames))
print("wrote", out, "peak before norm", round(peak, 3))
