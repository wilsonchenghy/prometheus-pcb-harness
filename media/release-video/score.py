"""The release video's soundtrack, synthesised from nothing (no samples, no licences): a pad and a pulse that build
with the film, and sound on its events - the title's copper reveal, the parts landing, the routing, each check
passing, each order file, each board under the inspection beam, the panel, the end card. Times are the film's own
(video.html). Writes score.wav (48 kHz stereo).

    python score.py && ffmpeg -i pcb-harness-release.mp4 -i score.wav -c:v copy -c:a aac -b:a 192k -shortest out.mp4
"""
import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt

SR, T_END = 48000, 74.0
N = int(SR * T_END)
rng = np.random.default_rng(7)
L = np.zeros(N)
R = np.zeros(N)
t_all = np.arange(N) / SR


def env(n, a=0.005, d=None, r=0.1, hold=0.0):
    """attack / hold / exponential release, n samples"""
    e = np.ones(n)
    na = max(1, int(a * SR))
    e[:na] = np.linspace(0, 1, na)
    nr = max(1, int(r * SR))
    start = min(n, na + int(hold * SR))
    tail = np.arange(n - start)
    e[start:] = np.exp(-tail / nr)
    return e


def put(sig, at, pan=0.0, gain=1.0):
    i = int(at * SR)
    if i >= N:
        return
    sig = sig[: N - i] * gain
    L[i:i + len(sig)] += sig * np.sqrt(0.5 * (1 - pan))
    R[i:i + len(sig)] += sig * np.sqrt(0.5 * (1 + pan))


def lp(x, f, order=2):
    return sosfilt(butter(order, f, "low", fs=SR, output="sos"), x)


def hp(x, f, order=2):
    return sosfilt(butter(order, f, "high", fs=SR, output="sos"), x)


def bp(x, lo, hi):
    return sosfilt(butter(2, [lo, hi], "band", fs=SR, output="sos"), x)


def hz(note):                       # MIDI note to Hz
    return 440.0 * 2 ** ((note - 69) / 12)


def saw(f, n, detune=0.0):
    ph = np.cumsum(np.full(n, f * (1 + detune)) / SR)
    return 2 * (ph % 1) - 1


def fade(a, b):                     # 0 -> 1 over the film's seconds a..b, as a whole-length curve
    return np.clip((t_all - a) / max(b - a, 1e-6), 0, 1)


# ---- the harmony: D minor, four chords a bar each at 112 BPM (a bar 2.14 s) ----
BPM = 112.0
BEAT = 60 / BPM
BAR = 4 * BEAT
CHORDS = [[50, 57, 60, 64, 65], [46, 53, 57, 62, 65], [41, 48, 53, 57, 64], [48, 55, 60, 64, 67]]  # Dm9 Bbmaj7 F C

# pad: detuned saws through a slow low-pass, chord by bar, swelling with the film
pad = np.zeros(N)
nb = int(np.ceil(T_END / BAR))
for b in range(nb):
    i0, i1 = int(b * BAR * SR), min(N, int((b + 1) * BAR * SR) + int(0.3 * SR))
    n = i1 - i0
    ch = CHORDS[b % 4]
    s = sum(saw(hz(m), n, d) for m in ch for d in (-0.004, 0.004)) / (2 * len(ch))
    e = np.minimum(1, np.minimum(np.arange(n) / (0.4 * SR), (n - np.arange(n)) / (0.3 * SR)))
    pad[i0:i1] += s * e
cut = 500 + 2200 * fade(16.0, 25.0) - 900 * fade(57.6, 63.0) + 500 * fade(63.4, 64.4) * (1 - fade(71.5, 74)) + 1100 * fade(70.4, 71.0) * (1 - fade(71.5, 74))
pad = sum(lp(pad, f) * ((cut > lo) & (cut <= hi)) for lo, hi, f in
          ((0, 700, 600), (700, 1300, 1000), (1300, 2000, 1600), (2000, 9999, 2600)))
pad = hp(pad, 220)                                  # (the bass has the low end to itself)
pad_gain = 0.05 + 0.12 * fade(0.0, 4.0) + 0.04 * fade(16, 24) - 0.06 * fade(71.5, 74.0)
L += pad * pad_gain * 0.9
R += np.roll(pad, int(0.011 * SR)) * pad_gain * 0.9

# drone under the opening and the close
dr = (np.sin(2 * np.pi * hz(38) * t_all) + 0.5 * np.sin(2 * np.pi * hz(50) * t_all + 0.3))
dr_g = 0.10 * (fade(0, 2.2) * (1 - fade(4.6, 6.0)) + fade(69.5, 70.4) * (1 - fade(72.5, 74)))
L += dr * dr_g
R += dr * dr_g

# ---- rhythm ----
def kick():
    n = int(0.45 * SR)
    tt = np.arange(n) / SR
    f = 46 + 120 * np.exp(-tt * 28)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 7.5)


def hat(open_=False):
    n = int((0.18 if open_ else 0.05) * SR)
    return hp(rng.standard_normal(n), 7000) * env(n, 0.001, r=0.05 if open_ else 0.012)


def pluck(note, dur=0.22, bright=2400):
    n = int(dur * SR)
    s = saw(hz(note), n) * 0.6 + saw(hz(note + 12), n, 0.003) * 0.25
    return lp(s, bright) * env(n, 0.002, r=dur / 3)


def bass(note, dur):
    n = int(dur * SR)
    tt = np.arange(n) / SR
    s = np.sin(2 * np.pi * hz(note) * tt) + 0.3 * np.tanh(3 * np.sin(2 * np.pi * hz(note) * tt))
    return s * env(n, 0.004, r=dur / 2.5)


def level(t):                       # the groove's energy by section
    if t < 4.6:
        return 0
    if t < 16.0:
        return 1                    # parts, placement: a soft pulse
    if t < 26.4:
        return 3                    # routing: everything
    if t < 38.6:
        return 2                    # checks, outputs
    if t < 53.4:
        return 3                    # the line
    if t < 57.6:
        return 2                    # the panel
    if t < 63.4:
        return 1                    # the agent loop: breathing
    if t < 70.4:
        return 2                    # the viewer
    return 0


step = BEAT / 4
k = 0
while k * step < T_END:
    t = k * step
    lv = level(t)
    bar = int(t // BAR)
    ch = CHORDS[bar % 4]
    if lv >= 1 and k % 2 == 0:      # eighth-note bass pulse
        put(lp(bass(ch[0] - 12, step * 1.8), 900), t, 0, 0.13 if lv == 1 else 0.16)
    if lv >= 2 and k % 4 == 0:
        put(kick(), t, 0, 0.42 if k % 16 in (0, 8) else 0.3)
    if lv >= 2 and k % 4 == 2:
        put(hat(), t, 0.3, 0.17)
    if lv >= 3:
        put(hat(open_=(k % 8 == 6)), t, -0.25, 0.07 + 0.05 * (k % 2 == 0))
        arp = [ch[1], ch[2], ch[3], ch[4], ch[3] + 12, ch[2], ch[4], ch[1] + 12]
        put(pluck(arp[k % 8] + 12, 0.2, 1800 + 1400 * ((k % 8) / 7)), t, 0.5 * np.sin(k * 0.7), 0.08)
    if lv == 1 and k % 8 == 4:
        put(pluck(ch[3] + 12, 0.35, 1200), t, -0.3, 0.05)
    k += 1


# ---- the film's events ----
def whoosh(dur=1.0, lo=300, hi=6000, rise=True):
    n = int(dur * SR)
    x = rng.standard_normal(n)
    tt = np.linspace(0, 1, n)
    out = np.zeros(n)
    bands = np.geomspace(lo, hi, 9)
    for j in range(8):
        g = np.exp(-((tt - (j / 7 if rise else 1 - j / 7)) ** 2) / 0.03)
        out += bp(x, bands[j], bands[j + 1]) * g
    return out * np.sin(np.pi * tt) ** 1.5


def hit(gain=1.0):                  # a deep impact with a bright transient
    n = int(2.5 * SR)
    tt = np.arange(n) / SR
    body = np.sin(2 * np.pi * np.cumsum(38 + 90 * np.exp(-tt * 14)) / SR) * np.exp(-tt * 2.2)
    snap = hp(rng.standard_normal(n), 2500) * np.exp(-tt * 40)
    return (body + 0.35 * snap) * gain


def chime(note, dur=1.2):
    n = int(dur * SR)
    tt = np.arange(n) / SR
    s = sum(a * np.sin(2 * np.pi * hz(note) * m * tt) * np.exp(-tt * (3 + 2 * m)) for m, a in
            ((1, 1), (2.0, 0.35), (3.01, 0.15), (4.2, 0.06)))
    return s * env(n, 0.002, r=dur)


def tick(f=3200, dur=0.03):
    n = int(dur * SR)
    tt = np.arange(n) / SR
    return np.sin(2 * np.pi * f * tt) * np.exp(-tt * 180)


def beep(f, dur=0.09):
    n = int(dur * SR)
    tt = np.arange(n) / SR
    return (np.sign(np.sin(2 * np.pi * f * tt)) * 0.3 + np.sin(2 * np.pi * f * tt)) * env(n, 0.002, r=0.03, hold=0.04)


# title: rise into the copper reveal, a glint shimmer
put(whoosh(2.2, 120, 3000), 0.1, 0, 0.16)
put(hit(0.55), 2.25, 0)
put(chime(74, 2.2), 2.3, -0.2, 0.08)
put(chime(81, 2.2), 2.35, 0.2, 0.06)
for j in range(10):                                         # the glint running along the letters
    put(chime(86 + (j % 3) * 2, 0.4), 3.3 + j * 0.12, -0.8 + 0.16 * j, 0.025)
# scene changes
for ts in (4.55, 15.3, 26.5, 32.5):
    put(whoosh(0.9, 400, 7000), ts - 0.45, 0.0, 0.11)
put(whoosh(1.4, 200, 8000), 38.0, 0, 0.16)                   # into the line
put(hit(0.35), 38.6, 0)
# the parts list types in: soft keys
for j in range(28):
    put(tick(2200 + 300 * (j % 3), 0.02), 5.2 + j * 0.24 + 0.05 * np.sin(j), 0.4 * np.sin(j * 1.3), 0.05)
# placement: each part landing (34 parts, 0.07 s apart from 12.6 s)
for j in range(34):
    put(tick(1400 + 90 * (j % 7), 0.04), 12.6 + j * 0.07, 0.8 * np.sin(j * 2.1), 0.07)
# routing: a rising sweep under the tracks, a shimmer as the pour fills
sweep_n = int(8.0 * SR)
tt = np.arange(sweep_n) / SR
sw = sum(np.sin(2 * np.pi * hz(62 + o) * (1 + 0.5 * tt / 8) * tt) for o in (0, 7, 12)) / 3
put(lp(sw, 2500) * np.sin(np.pi * tt / 8) ** 2, 16.6, 0, 0.035)
put(whoosh(1.6, 800, 9000, rise=False), 25.0, 0, 0.08)
# checks: one chime per judge passing, rising
for i, note in enumerate((74, 77, 81)):
    put(chime(note, 1.4), 28.2 + i * 0.9, -0.4 + 0.4 * i, 0.13)
    put(tick(5200, 0.02), 28.2 + i * 0.9, 0, 0.1)
# outputs: a click per file
for i in range(6):
    put(tick(2600 + 200 * i, 0.035), 33.7 + i * 0.35, -0.5 + 0.2 * i, 0.1)
    put(chime(86 + [0, 2, 3, 5, 7, 10][i], 0.5), 33.7 + i * 0.35, -0.5 + 0.2 * i, 0.03)
# the line: a scan beep as each board meets the beam, a stamp as it leaves (v 680, spacing 780, from 0.3 s)
for i in range(11):
    t_in = 38.6 + 0.3 + (1920 + 330 + i * 780 - 960 - 320) / 680
    if t_in > 53.3:
        break
    put(beep(1760, 0.07), t_in, 0.0, 0.05)
    put(beep(2349, 0.07), t_in + 0.09, 0.0, 0.04)
    put(hit(0.12) * 0.6, t_in + 1.05, 0.15, 1.0)
    put(tick(900, 0.06), t_in + 1.05, 0.15, 0.12)
# the panel: an impact and the count rising
put(hit(0.5), 53.55, 0)
put(chime(69, 2.5), 53.6, 0, 0.07)
for j in range(20):
    put(tick(1800 + j * 90, 0.02), 53.9 + j * 0.1, 0, 0.05)
# the agent loop: four steps round the ring
for j in range(4):
    put(chime([74, 76, 77, 81][j], 0.9), 58.2 + j * 1.1, [-0.6, -0.2, 0.2, 0.6][j], 0.07)
# the viewer: into the browser, a chime as each view comes up
put(whoosh(1.2, 300, 7000), 62.4, 0, 0.09)
put(hit(0.35), 63.4, 0)
for j, ts in enumerate((63.9, 66.1, 68.3)):
    put(chime([81, 83, 86][j], 1.2), ts, [-0.4, 0.0, 0.4][j], 0.07)
    put(tick(4200, 0.02), ts, 0, 0.08)
# the end card: the last hit, a wide final chord ringing out
put(whoosh(1.6, 150, 6000), 69.2, 0, 0.13)
put(hit(0.7), 70.4, 0)
end_n = int(3.6 * SR)
tt = np.arange(end_n) / SR
fin = sum(saw(hz(m), end_n, d) for m in (38, 50, 57, 62, 65, 69) for d in (-0.003, 0.003)) / 12
put(lp(fin, 1800) * np.exp(-tt * 0.9) * np.minimum(1, tt / 0.05), 70.4, 0, 0.22)
put(chime(86, 3.0), 70.45, 0.3, 0.06)

# ---- room, glue, level ----
ir_n = int(2.2 * SR)
tt = np.arange(ir_n) / SR
irL = rng.standard_normal(ir_n) * np.exp(-tt * 2.6)
irR = rng.standard_normal(ir_n) * np.exp(-tt * 2.6)
irL, irR = lp(irL, 5000), lp(irR, 5000)
irL[: int(0.012 * SR)] = 0
irR[: int(0.017 * SR)] = 0
wetL = fftconvolve(L, irL)[:N] / np.sqrt(np.sum(irL ** 2)) * 0.18
wetR = fftconvolve(R, irR)[:N] / np.sqrt(np.sum(irR ** 2)) * 0.18
L, R = hp(L + wetL, 28), hp(R + wetR, 28)
master = np.minimum(1, t_all / 0.3) * np.clip((T_END - t_all) / 1.0, 0, 1)   # the film's own fades
L, R = L * master, R * master
peak = max(np.abs(L).max(), np.abs(R).max())
L, R = np.tanh(1.4 * L / peak) / np.tanh(1.4), np.tanh(1.4 * R / peak) / np.tanh(1.4)     # soft limit
L, R = L * 0.89, R * 0.89                                                                # (-1 dBFS)

import wave  # noqa: E402
with wave.open("score.wav", "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((np.stack([L, R], 1) * 32767).astype("<i2").tobytes())
print(f"score.wav: {T_END:.0f} s, peak {max(np.abs(L).max(), np.abs(R).max()):.2f}")
