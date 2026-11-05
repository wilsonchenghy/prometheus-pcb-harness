"""The release video's soundtrack (30.2 s, 128 BPM), synthesised from nothing (no samples, no licences): a pad and a pulse that build
with the film, and sound on its events - the title's copper reveal, the parts landing, the routing, each check
passing, each order file, each board under the inspection beam, the panel, the end card. Times are the film's own
(video.html). Writes score.wav (48 kHz stereo).

    python score.py && ffmpeg -i prometheus-release.mp4 -i score.wav -c:v copy -c:a aac -b:a 192k -shortest out.mp4
"""
import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt

SR, T_END = 48000, 30.2
# the cut's pace (as video.html's PACE): the times below are the scene clock's, mapped to the film's
PACE = [(0, 0), (3.4, 3.4), (6.4, 8.0), (8.7, 11.4), (12.0, 16.6), (15.4, 19.6), (15.9, 20.4), (18.6, 24.45), (22.0, 28.35), (22.1, 28.5), (26.5, 35.5), (30.2, 39.9)]
HOLD, HOLD_LEN = 16.6, 3.0
MONT, MONT_LEN = 21.45, 3.9        # the "more" cards: the scene clock waits again
MONT_X = MONT + HOLD_LEN         # the chat after routing: the scene clock waits


def M(x, held=False):               # scene clock -> film seconds (held: a time inside the chat's hold)
    if not held:
        x = x if x <= HOLD else x + HOLD_LEN if x <= MONT else x + HOLD_LEN + MONT_LEN
    for (a, A), (b, B) in zip(PACE, PACE[1:]):
        if x <= B:
            return a + (x - A) / (B - A) * (b - a)
    return T_END
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


def put(sig, at, pan=0.0, gain=1.0, film=False, held=False):
    i = int((at if film else M(at, held)) * SR)
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


def fade(a, b):                     # 0 -> 1 over the scene's seconds a..b, as a whole-length curve
    a, b = M(a), M(b)
    return np.clip((t_all - a) / max(b - a, 1e-6), 0, 1)


# ---- the harmony: D minor, four chords a bar each at 128 BPM (a bar 1.88 s) ----
BPM = 128.0
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
cut = 500 + 2000 * fade(10.0, 17.0) - 700 * fade(17.4, 21.0) + 900 * fade(21.6, 23.0) * (1 - fade(30.5, 33))
pad = sum(lp(pad, f) * ((cut > lo) & (cut <= hi)) for lo, hi, f in
          ((0, 700, 600), (700, 1300, 1000), (1300, 2000, 1600), (2000, 9999, 2600)))
pad = hp(pad, 220)                                  # (the bass has the low end to itself)
pad_gain = 0.05 + 0.12 * fade(0.0, 2.5) + 0.04 * fade(11, 16) - 0.07 * fade(31.0, 33.0)
L += pad * pad_gain * 0.9
R += np.roll(pad, int(0.011 * SR)) * pad_gain * 0.9

# drone under the opening and the close
dr = (np.sin(2 * np.pi * hz(38) * t_all) + 0.5 * np.sin(2 * np.pi * hz(50) * t_all + 0.3))
dr_g = 0.10 * (fade(0, 1.5) * (1 - fade(3.2, 4.4)) + fade(28.0, 28.6) * (1 - fade(31.5, 33)))
L += dr * dr_g
R += dr * dr_g

# ---- rhythm ----
def paced(t):                       # film seconds -> scene clock
    x = 39.9
    for (a, A), (b, B) in zip(PACE, PACE[1:]):
        if t <= b:
            x = A + (t - a) / (b - a) * (B - A)
            break
    if x <= HOLD:
        return x
    if x <= HOLD + HOLD_LEN:
        return HOLD
    if x <= MONT_X:
        return x - HOLD_LEN
    if x <= MONT_X + MONT_LEN:
        return MONT
    return x - HOLD_LEN - MONT_LEN


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


def level(t):                       # the groove's energy by beat (t: film seconds)
    t = paced(t)
    if t < 3.4:
        return 0                    # the hook
    if t < 8.0:
        return 1                    # the ask
    if t < 11.4:
        return 2                    # placed
    if t < 17.4:
        return 3                    # routed: everything
    if t < 21.6:
        return 2                    # checked
    if t < 28.6:
        return 3                    # the boards
    return 0


step = BEAT / 4
k = 0
while k * step < T_END:
    t = k * step
    lv = level(t)
    bar = int(t // BAR)
    ch = CHORDS[bar % 4]
    if lv >= 1 and k % 2 == 0:      # eighth-note bass pulse
        put(lp(bass(ch[0] - 12, step * 1.8), 900), t, 0, 0.13 if lv == 1 else 0.16, film=True)
    if lv >= 2 and k % 4 == 0:
        put(kick(), t, 0, 0.42 if k % 16 in (0, 8) else 0.3, film=True)
    if lv >= 2 and k % 4 == 2:
        put(hat(), t, 0.3, 0.17, film=True)
    if lv >= 3:
        put(hat(open_=(k % 8 == 6)), t, -0.25, 0.07 + 0.05 * (k % 2 == 0), film=True)
        arp = [ch[1], ch[2], ch[3], ch[4], ch[3] + 12, ch[2], ch[4], ch[1] + 12]
        put(pluck(arp[k % 8] + 12, 0.2, 1800 + 1400 * ((k % 8) / 7)), t, 0.5 * np.sin(k * 0.7), 0.08, film=True)
    if lv == 1 and k % 8 == 4:
        put(pluck(ch[3] + 12, 0.35, 1200), t, -0.3, 0.05, film=True)
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


# the hook: a hit on each hard cut, the name slams in on the downbeat
B = 60 / 112
for j in range(4):
    put(hit(0.35 + 0.08 * j), j * B, 0)
    put(tick(2600 + 300 * j, 0.03), j * B, -0.3 + 0.2 * j, 0.12)
put(whoosh(0.5, 300, 9000), 4 * B - 0.5, 0, 0.12)
put(hit(0.7), 4 * B, 0)
put(chime(74, 2.0), 4 * B + 0.02, -0.2, 0.045)
put(chime(81, 2.0), 4 * B + 0.06, 0.2, 0.035)
# the ask: a key for each few characters
for j in range(36):
    put(tick(2600 + 300 * (j % 3), 0.02), 3.9 + j * 2.4 / 36, 0.3 * np.sin(j), 0.045)
# the ask's phrases fly to their parts: a bright tick as each lands
for j, done in enumerate((3.9 + 2.4 * 16 / 92, 3.9 + 2.4 * 29 / 92, 3.9 + 2.4 * 36 / 92, 3.9 + 2.4 * 60 / 92)):
    put(tick(4200, 0.02), done + 0.62, 0, 0.08)
# you approve each step: a soft click and a confirming chime
for T0 in (10.6, 16.6, 20.3):
    put(tick(1600, 0.025), T0 + 0.56, 0.4, 0.14)
# placed: each part landing
put(whoosh(0.9, 400, 7000), M(8.0) - 0.6, 0, 0.1, film=True)
for j in range(34):
    put(tick(1400 + 90 * (j % 7), 0.04), 8.6 + j * 0.05, 0.8 * np.sin(j * 2.1), 0.06)
# routed: a rising sweep under the copper, the pour
put(whoosh(0.9, 400, 7000), M(11.4) - 0.5, 0, 0.1, film=True)
tt = np.arange(int(2.9 * SR)) / SR
sw = np.sin(2 * np.pi * np.cumsum(180 + 700 * tt / 2.9) / SR) * 0.4
put(lp(sw, 2500) * np.sin(np.pi * tt / 2.9) ** 2, 12.0, 0, 0.03)
put(whoosh(1.2, 800, 9000, rise=False), 16.4, 0, 0.08)
# the chat: your follow-up typed, Prometheus answers and the power traces widen
for j in range(14):
    put(tick(2600 + 300 * (j % 3), 0.02), HOLD + 0.2 + j * 0.8 / 14, -0.3 * np.sin(j), 0.04, held=True)
put(whoosh(0.5, 300, 5000), HOLD + 0.7, 0, 0.07, held=True)
put(lp(pluck(62, 0.5, 1400), 1600), HOLD + 1.15, 0, 0.12, held=True)
put(lp(bass(38, 0.6), 600), HOLD + 1.15, 0, 0.18, held=True)
for j, at in enumerate((2.2, 2.35)):          # the checks re-run: soft clicks
    put(tick(1500 + 150 * j, 0.025), HOLD + at, 0.3, 0.09, held=True)
# the clash: a low warning, then resolved
put(lp(pluck(45, 0.4, 700), 900), HOLD + 1.6, 0, 0.16, held=True)
put(lp(pluck(57, 0.4, 1400), 1600), HOLD + 1.95, 0, 0.12, held=True)
# you may pin a part: the connector dragged
put(whoosh(0.7, 300, 3000), 7.85, 0.3, 0.06)
put(tick(1700, 0.03), 8.65, 0.3, 0.12)
# the "more" cards: a soft whoosh as each slides in
for j in range(4):
    put(whoosh(0.35, 500, 6000), MONT_X + 0.15 + j * 1.2 - 0.1, 0.4 - 0.16 * j, 0.06, held=True)
# checked: a chime as each check passes
for i, m in enumerate((74, 76, 77, 79, 81)):
    put(lp(pluck(m - 12, 0.25, 900), 1200), 17.85 + i * 0.37, -0.4 + 0.2 * i, 0.10)
    put(tick(5200, 0.02), 17.85 + i * 0.37, 0, 0.1)
# the boards: the panel lands, its eight tiles click in
put(whoosh(1.4, 200, 8000), M(21.6) - 0.8, 0, 0.15, film=True)
put(hit(0.5), 22.6, 0)                  # the board lands in its slot
put(chime(69, 2.5), 21.8, 0, 0.035)
for j in range(8):
    put(tick(1800 + j * 120, 0.02), 22.05 + j * 0.08, -0.5 + j / 7, 0.06)
# the end card: the last hit, a wide chord ringing out
put(whoosh(1.6, 150, 6000), M(28.6) - 1.2, 0, 0.13, film=True)
put(hit(0.7), 28.6, 0)
end_n = int(4.2 * SR)
tt = np.arange(end_n) / SR
fin = sum(saw(hz(m), end_n, d) for m in (38, 50, 57, 62, 65, 69) for d in (-0.003, 0.003)) / 12
put(lp(fin, 1800) * np.exp(-tt * 0.9) * np.minimum(1, tt / 0.05), 28.6, 0, 0.22)
put(chime(86, 3.0), 28.65, 0.3, 0.03)
# the end card: the install line typed, then sent
for j in range(22):
    put(tick(2400 + 200 * (j % 4), 0.02), 28.6 + (46.0 - 44.4) + j * 0.9 / 22, 0.2 * np.sin(j), 0.05)

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

