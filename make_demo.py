"""Make listenable demo assets from existing results.

  docs/audio/*.wav   loudness-matched clips: the original sentence and what came back from the sensor
  docs/demo.mp4      the clips in order, each over its spectrogram with a moving playhead
                     (GitHub plays video, not .wav, and the same file works on LinkedIn / X)

Inputs come from earlier runs: audio/shift_ref16.wav (python3 make_audio.py --factor --shift 16),
data/speech10_accelerometer.wav, data/shift16_accelerometer.wav, data/cshift16_accelerometer.wav
(or a saved data/cshift16_accelerometer_table.wav, which takes precedence).
"""
import subprocess
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.io import wavfile
from scipy.signal import spectrogram

ROOT = Path(__file__).parent
OUT_AUDIO = ROOT / "docs" / "audio"
WORK = ROOT / "build" / "demo"
W, H, FPS = 1280, 720, 30
AX = (0.07, 0.10, 0.90, 0.62)          # spectrogram axes: left, bottom, width, height (figure fraction)
REPO = "github.com/pradeeppc/accel-voice"

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


def load_norm(path, rms_db):
    """Read a clip and match its loudness (RMS) without letting peaks clip."""
    fs, x = wavfile.read(path)
    x = x.astype(float) / 32768
    rms = np.sqrt(np.mean(x ** 2)) + 1e-12
    gain = min(10 ** (rms_db / 20) / rms, 0.98 / (np.max(np.abs(x)) + 1e-12))
    return fs, x * gain


def frame(path, title, sub, x=None, fs=None):
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor="white")
    fig.text(0.07, 0.92, title, fontsize=26, weight="bold", va="center")
    fig.text(0.07, 0.84, sub, fontsize=16, color="#444", va="center")
    if x is None:   # title card
        fig.text(0.07, 0.62, "Speech picked up by a phone's\naccelerometer, not its microphone",
                 fontsize=34, weight="bold", va="center")
        fig.text(0.07, 0.40, "Inspired by Deepinder Goyal's Temple", fontsize=22, color="#444")
        fig.text(0.07, 0.15, REPO, fontsize=20, color="#0a7")
    else:
        ax = fig.add_axes(AX)
        f, t, s = spectrogram(x, fs, nperseg=512, noverlap=384)
        keep = f <= 2000
        db = 10 * np.log10(s[keep] + 1e-20)
        ax.pcolormesh(t, f[keep], db, shading="auto", cmap="magma", vmin=db.max() - 60, vmax=db.max())
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


def main():
    OUT_AUDIO.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    parts = []

    card = WORK / "card.png"
    frame(card, "", "")
    seg = WORK / "seg0.mp4"
    ffmpeg("-loop", "1", "-i", card, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
           "-t", "3", *ENC, seg)
    parts.append(seg)

    x0, x1 = AX[0] * W, (AX[0] + AX[2]) * W
    y0, y1 = (1 - AX[1] - AX[3]) * H, (1 - AX[1]) * H
    for i, (src, name, rms_db, title, sub) in enumerate(CLIPS, 1):
        if isinstance(src, tuple):      # first file that exists
            src = next(c for c in src if (ROOT / c).exists())
        fs, x = load_norm(ROOT / src, rms_db)
        wav = OUT_AUDIO / name
        wavfile.write(wav, fs, (x * 32767).astype(np.int16))
        png = WORK / f"f{i}.png"
        frame(png, title, sub, x, fs)
        dur = len(x) / fs
        seg = WORK / f"seg{i}.mp4"
        # Playhead: a thin bar overlaid on the still frame, sliding across the spectrogram axes.
        move = f"overlay=x='{x0:.0f}+{x1 - x0:.0f}*min(t/{dur:.3f}\\,1)':y={y0:.0f}:eval=frame"
        ffmpeg("-loop", "1", "-i", png, "-i", wav,
               "-f", "lavfi", "-i", f"color=c=0x00e5ff:s=3x{y1 - y0:.0f}",
               "-filter_complex", f"[0:v][2:v]{move}[v];[1:a]apad=pad_dur=0.8[a]",
               "-map", "[v]", "-map", "[a]", "-t", f"{dur + 0.8:.3f}", *ENC, seg)
        parts.append(seg)
        print(f"{name:38s} {dur:4.1f} s")

    lst = WORK / "parts.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    out = ROOT / "docs" / "demo.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
