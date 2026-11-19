"""Make listenable demo assets from existing results.

  docs/audio/*.wav     loudness-matched clips
  docs/demo.mp4        the test sentence and what came back from the accelerometer
  docs/placements.mp4  real voice through bone (hum, then count 1-10) at the PLACES below
Each video shows every clip over its spectrogram with a moving playhead. GitHub plays video,
not .wav, and the same files work on LinkedIn / X.

Inputs come from earlier runs: audio/shift_ref16.wav (python3 make_audio.py --factor --shift 16),
data/speech10_accelerometer.wav, data/shift16_accelerometer.wav, data/cshift16_accelerometer.wav
(or a saved data/cshift16_accelerometer_table.wav, which takes precedence), and
data/hum_<place>_accelerometer.wav from ./hum_test.sh <place>.
"""
import subprocess
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfiltfilt, spectrogram

from analyze import load, uniform

ROOT = Path(__file__).parent
OUT_AUDIO = ROOT / "docs" / "audio"
WORK = ROOT / "build" / "demo"
W, H, FPS = 1280, 720, 30
AX = (0.07, 0.10, 0.90, 0.62)          # spectrogram axes: left, bottom, width, height (figure fraction)
REPO = "github.com/pradeeppc/accel-voice-temple-inspired"

CLIPS = [
    ("audio/shift_ref16.wav", "1_original.wav", -20,
     "1. The original sentence",
     "Text-to-speech, limited to 250-1900 Hz. This is what we try to get back."),
    ("data/speech10_accelerometer.wav", "2_slowed10x_failed.wav", -26,
     "2. Slowed 10x, recovered from the accelerometer: fails",
     "The phone's speaker can't shake the phone below ~120 Hz, so only noise comes back."),
    ("data/shift16_accelerometer.wav", "3_shifted_recovered.wav", -20,
     "3. Slowed 16x and shifted to 285-390 Hz: recovered",
     "The sensor's aliasing folds it into its 0-199 Hz range; the analysis unfolds it."),
    (("data/cshift16_accelerometer_table.wav", "data/cshift16_accelerometer.wav"),
     "4_shifted_companded_recovered.wav", -20,
     "4. Same, plus pre-emphasis and companding",
     "The pauses go quiet. Some hiss remains inside the words."),
]

# Real-voice recordings to include, from ./hum_test.sh <place>. Times are hum_test.sh's script.
PLACES = ["throat", "cheekbone"]
HUM, COUNT, QUIET = (8, 14), (16.5, 24), (26, 29.5)
KEEP = (7.5, 24.5)                     # trim each recording to the hum and the counting


def normalize(x, rms_db):
    """Match loudness (RMS) without letting peaks clip."""
    rms = np.sqrt(np.mean(x ** 2)) + 1e-12
    return x * min(10 ** (rms_db / 20) / rms, 0.98 / (np.max(np.abs(x)) + 1e-12))


def read(path):
    fs, x = wavfile.read(path)
    return fs, x.astype(float) / 32768


def frame(path, title, sub, x=None, fs=None, fmax=2000, nperseg=512, marks=(), card=None):
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor="white")
    if card:
        fig.text(0.07, 0.62, card[0], fontsize=34, weight="bold", va="center")
        fig.text(0.07, 0.40, card[1], fontsize=22, color="#444")
        fig.text(0.07, 0.15, REPO, fontsize=20, color="#0a7")
    else:
        fig.text(0.07, 0.92, title, fontsize=26, weight="bold", va="center")
        fig.text(0.07, 0.84, sub, fontsize=16, color="#444", va="center")
        ax = fig.add_axes(AX)
        f, t, s = spectrogram(x, fs, nperseg=nperseg, noverlap=nperseg * 3 // 4)
        keep = f <= fmax
        db = 10 * np.log10(s[keep] + 1e-20)
        ax.pcolormesh(t, f[keep], db, shading="auto", cmap="magma", vmin=db.max() - 60, vmax=db.max())
        for label, a, b in marks:
            ax.axvspan(a, b, color="cyan", alpha=0.08)
            ax.text((a + b) / 2, fmax * 0.93, label, color="cyan", ha="center", fontsize=14)
        ax.set_xlim(0, len(x) / fs)
        ax.set_ylabel("Hz", fontsize=12)
        ax.set_xlabel("seconds", fontsize=12)
        fig.text(0.97, 0.03, REPO, fontsize=12, color="#0a7", ha="right")
    fig.savefig(path, dpi=100, facecolor="white")
    plt.close(fig)


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


ENC = ["-r", str(FPS), "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1"]


def build_video(name, card, items):
    """items: dicts with x, fs, wav (output name), title, sub and optional fmax/nperseg/marks."""
    work = WORK / name
    work.mkdir(parents=True, exist_ok=True)
    png = work / "card.png"
    frame(png, "", "", card=card)
    parts = [work / "seg0.mp4"]
    ffmpeg("-loop", "1", "-i", png, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
           "-t", "3", *ENC, parts[0])

    x0, x1 = AX[0] * W, (AX[0] + AX[2]) * W
    y0, y1 = (1 - AX[1] - AX[3]) * H, (1 - AX[1]) * H
    for i, it in enumerate(items, 1):
        wav = OUT_AUDIO / it["wav"]
        wavfile.write(wav, it["fs"], (it["x"] * 32767).astype(np.int16))
        png = work / f"f{i}.png"
        frame(png, it["title"], it["sub"], it["x"], it["fs"], it.get("fmax", 2000),
              it.get("nperseg", 512), it.get("marks", ()))
        dur = len(it["x"]) / it["fs"]
        seg = work / f"seg{i}.mp4"
        # Playhead: a thin bar overlaid on the still frame, sliding across the spectrogram axes.
        move = f"overlay=x='{x0:.0f}+{x1 - x0:.0f}*min(t/{dur:.3f}\\,1)':y={y0:.0f}:eval=frame"
        ffmpeg("-loop", "1", "-i", png, "-i", wav,
               "-f", "lavfi", "-i", f"color=c=0x00e5ff:s=3x{y1 - y0:.0f}",
               "-filter_complex", f"[0:v][2:v]{move}[v];[1:a]apad=pad_dur=0.8[a]",
               "-map", "[v]", "-map", "[a]", "-t", f"{dur + 0.8:.3f}", *ENC, seg)
        parts.append(seg)
        print(f"  {it['wav']:38s} {dur:4.1f} s")

    lst = work / "parts.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    out = ROOT / "docs" / f"{name}.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", out)
    print(f"wrote {out}")


def sentence_items():
    items = []
    for src, wav, rms_db, title, sub in CLIPS:
        if isinstance(src, tuple):      # first file that exists
            src = next(c for c in src if (ROOT / c).exists())
        fs, x = read(ROOT / src)
        items.append(dict(x=normalize(x, rms_db), fs=fs, wav=wav, title=title, sub=sub))
    return items


def voice_levels(csv):
    """Voice level above quiet (dB) for the hum and the counting, computed exactly as
    analyze.py --kind hum does: 70-195 Hz band, the accelerometer axis with the most voice."""
    sensors, _ = load(csv)
    t, xyz = sensors["a"]
    tg, xyz, fs = uniform(t, xyz)
    v = sosfiltfilt(butter(4, [70, min(195, 0.49 * fs)], "bandpass", fs=fs, output="sos"), xyz, axis=0)
    seg = lambda a, b: (tg >= a) & (tg < b)
    quiet, loud = seg(*QUIET), seg(*HUM) | seg(*COUNT)
    i = int(np.argmax([np.mean(v[loud, k] ** 2) / np.mean(v[quiet, k] ** 2) for k in range(3)]))
    return [10 * np.log10(np.mean(v[seg(*s_), i] ** 2) / np.mean(v[quiet, i] ** 2)) for s_ in (HUM, COUNT)]


def place_items(first_number):
    items = []
    for n, place in enumerate(PLACES, first_number):
        src = ROOT / "data" / f"hum_{place}_accelerometer.wav"
        if not src.exists():
            print(f"  skipping {place}: no {src.relative_to(ROOT)}")
            continue
        fs, x = read(src)
        hum, count = voice_levels(ROOT / "data" / f"hum_{place}.csv")
        x = normalize(x[int(KEEP[0] * fs):int(KEEP[1] * fs)], -22)
        items.append(dict(
            x=x, fs=fs, wav=f"{n}_voice_{place}.wav", fmax=200, nperseg=4096,
            title=f"{place.capitalize()}: real voice through bone",
            sub=f"Hum, then count 1-10. Voice {hum:+.0f} dB (hum) / {count:+.0f} dB (count) above quiet."
                " Use headphones.",
            marks=[("hum", HUM[0] - KEEP[0], HUM[1] - KEEP[0]),
                   ("count", COUNT[0] - KEEP[0], COUNT[1] - KEEP[0])]))
    return items


def placements_image():
    """docs/placements.png: one accelerometer spectrogram per place in PLACES (README picture)."""
    places = [p for p in PLACES if (ROOT / "data" / f"hum_{p}.csv").exists()]
    if not places:
        return
    fig, axes = plt.subplots(len(places), 1, figsize=(10, 2.5 * len(places)), sharex=True, squeeze=False)
    for ax, place in zip(axes[:, 0], places):
        sensors, _ = load(ROOT / "data" / f"hum_{place}.csv")
        t, xyz = sensors["a"]
        tg, xyz, fs = uniform(t, xyz)
        f, tt, s = spectrogram(xyz[:, 2], fs, nperseg=128, noverlap=96)
        db = 10 * np.log10(s + 1e-20)
        ax.pcolormesh(tt + tg[0], f, db, shading="auto", cmap="magma", vmin=db.max() - 60, vmax=db.max())
        ax.set_title(f"{place} (accelerometer z)", fontsize=10)
        ax.set_ylabel("Hz")
        for label, (a, b) in (("hum", HUM), ("count", COUNT)):
            ax.axvspan(a, b, color="cyan", alpha=0.08)
            ax.text((a + b) / 2, 185, label, color="cyan", ha="center", fontsize=9)
    axes[-1, 0].set_xlabel("seconds")
    fig.tight_layout()
    out = ROOT / "docs" / "placements.png"
    fig.savefig(out, dpi=100)
    plt.close(fig)
    print(f"wrote {out}")


def main():
    OUT_AUDIO.mkdir(parents=True, exist_ok=True)
    build_video("demo", ("Speech picked up by a phone's\naccelerometer, not its microphone",
                         "Inspired by Deepinder Goyal's Temple"), sentence_items())
    placements_image()
    items = place_items(len(CLIPS) + 1)
    if items:
        build_video("placements", ("Real voice through bone,\npicked up by a phone's accelerometer",
                                   "Hum, then count from one to ten,\nphone pressed to the "
                                   + " and the ".join(PLACES)), items)


if __name__ == "__main__":
    main()
