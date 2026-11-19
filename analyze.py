"""Turn a recorded sensor CSV back into audio and plot what the sensor heard.

  python3 analyze.py data/sweep.csv --kind sweep
  python3 analyze.py data/speech10.csv --kind speech --factor 10
  python3 analyze.py data/shift16.csv --kind shift --factor 16
  python3 analyze.py data/hum.csv --kind raw
"""
import argparse
import json
from fractions import Fraction
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.io import wavfile
from scipy.signal import (butter, correlate, hilbert, istft, lfilter, resample_poly, sosfiltfilt,
                          spectrogram, stft)

from make_audio import compand_gain

ROOT = Path(__file__).parent
OUT_FS = 16000


def load(path):
    df = pd.read_csv(path)
    marks = {r.sensor: r.t_ns for r in df[df.sensor.str.startswith("play")].itertuples()}
    t0 = df[df.sensor.isin(["a", "g"])].t_ns.min()
    sensors = {}
    for kind in ("a", "g"):
        d = df[df.sensor == kind].sort_values("t_ns").drop_duplicates("t_ns")
        t = (d.t_ns.to_numpy() - t0) / 1e9
        sensors[kind] = (t, d[["x", "y", "z"]].to_numpy(float))
    marks = {k: (v - t0) / 1e9 for k, v in marks.items()}
    return sensors, marks


def uniform(t, xyz):
    """Resample jittery sensor timestamps onto an even grid at the measured rate."""
    fs = round(1 / np.median(np.diff(t)), 2)
    tg = np.arange(t[0], t[-1], 1 / fs)
    xyz = np.column_stack([np.interp(tg, t, xyz[:, i]) for i in range(3)])
    xyz = sosfiltfilt(butter(4, 2.0, "highpass", fs=fs, output="sos"), xyz, axis=0)  # drop gravity/drift
    return tg, xyz, fs


def best_axis(tg, xyz, marks):
    """Project the 3 axes onto the direction that vibrates most during playback (PCA)."""
    play = (tg >= marks.get("play_start", tg[0])) & (tg <= marks.get("play_end", tg[-1]))
    _, _, vt = np.linalg.svd(xyz[play] - xyz[play].mean(0), full_matrices=False)
    sig = xyz @ vt[0]
    lead = tg < marks.get("play_start", 0)
    snr = 10 * np.log10(np.mean(sig[play] ** 2) / np.mean(sig[lead] ** 2)) if lead.sum() > 50 else float("nan")
    return sig, play, snr


def sensor_band(play_band, fs):
    """Where a played band lands in the sensor's 0..fs/2 range, and whether it arrives mirrored."""
    lo, hi = play_band
    if hi <= fs / 2:
        return (lo, hi), False
    if lo >= fs / 2 and hi <= fs:
        return (fs - hi, fs - lo), True
    raise ValueError(f"play band {play_band} straddles the sensor's {fs / 2:.0f} Hz fold")


def best_band_axis(tg, xyz, marks, band, fs):
    """Pick the axis (x, y, z or PCA) with the most in-band power during playback vs silence."""
    sos = butter(6, band, "bandpass", fs=fs, output="sos")
    filt = sosfiltfilt(sos, xyz, axis=0)
    play = (tg >= marks["play_start"]) & (tg <= marks["play_end"] + 1.0)
    lead = tg < marks["play_start"]
    _, _, vt = np.linalg.svd(filt[play] - filt[play].mean(0), full_matrices=False)
    cands = {"x": filt[:, 0], "y": filt[:, 1], "z": filt[:, 2], "pca": filt @ vt[0]}
    snr = {k: 10 * np.log10(np.mean(v[play] ** 2) / np.mean(v[lead] ** 2)) for k, v in cands.items()}
    k = max(snr, key=snr.get)
    return k, cands[k], play, lead, snr[k]


def denoise(sig, noise, fs, n=64):
    """Spectral subtraction: remove the steady background measured during the silent lead-in."""
    _, _, Z = stft(sig, fs, nperseg=n)
    _, _, N = stft(noise, fs, nperseg=n)
    floor = np.mean(np.abs(N), axis=1, keepdims=True)
    mag = np.maximum(np.abs(Z) - 1.5 * floor, 0.05 * np.abs(Z))
    _, y = istft(mag * np.exp(1j * np.angle(Z)), fs, nperseg=n)
    return y[: len(sig)]


def unshift(sig, fs, shift, mirrored):
    """Undo the single-sideband shift (and the alias mirror) to get the slowed speech back."""
    t = np.arange(len(sig)) / fs
    z = hilbert(sig)
    if mirrored:                       # sensor saw fs - f: flip the spectrum back
        z, shift = np.conj(z), shift - fs
    return np.real(z * np.exp(-2j * np.pi * shift * t))


def speed_up(sig, fs_in, speedup=1):
    """Treat sensor samples as audio sped up `speedup` times (undoes the slow-down), at OUT_FS."""
    ratio = Fraction(OUT_FS / (fs_in * speedup)).limit_denominator(2000)
    return resample_poly(sig, ratio.numerator, ratio.denominator)


def write_wav(y, path):
    y = y / (np.max(np.abs(y)) + 1e-12) * 0.9
    wavfile.write(path, OUT_FS, (y * 32767).astype(np.int16))
    return y


def to_wav(sig, fs_in, path, speedup=1):
    """Write sensor samples as audio, sped up `speedup` times (undoes the slow-down)."""
    y = write_wav(speed_up(sig, fs_in, speedup), path)
    wavfile.write(path, OUT_FS, (y * 32767).astype(np.int16))
    return y


def score(ref, y, band):
    """How close recovered audio is to the original (both at OUT_FS).
    gap: dB between speech level and the level in the pauses (higher = quieter background).
    match: correlation of log spectrograms over speech frames (1.0 = identical)."""
    lag = np.argmax(correlate(np.abs(y), np.abs(ref), mode="full")) - (len(ref) - 1)
    y = np.pad(y, (max(0, -lag), 0))[max(0, lag):][: len(ref)]
    y = np.pad(y, (0, len(ref) - len(y)))
    f, _, A = stft(ref, OUT_FS, nperseg=512)
    _, _, B = stft(y, OUT_FS, nperseg=512)
    k = (f >= band[0]) & (f <= band[1])
    pa, pb = np.sum(np.abs(A[k]) ** 2, 0), np.sum(np.abs(B[k]) ** 2, 0)
    da = 10 * np.log10(pa + 1e-20)
    speech, pause = da > da.max() - 25, da < da.max() - 45
    gap = 10 * np.log10(pb[speech].mean() / (pb[pause].mean() + 1e-30))
    la, lb = np.log(np.abs(A[k][:, speech]) + 1e-6), np.log(np.abs(B[k][:, speech]) + 1e-6)
    return gap, np.corrcoef(la.ravel(), lb.ravel())[0, 1]


def spec(ax, x, fs, title, fmax, nperseg=None):
    nperseg = nperseg or max(64, int(fs * 0.032))
    f, t, s = spectrogram(x, fs, nperseg=nperseg, noverlap=nperseg * 3 // 4)
    keep = f <= fmax
    db = 10 * np.log10(s[keep] + 1e-20)
    ax.pcolormesh(t, f[keep], db, shading="auto", cmap="magma", vmin=db.max() - 60, vmax=db.max())
    ax.set_title(title, fontsize=10)
    ax.set_ylabel("Hz")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--kind", choices=["sweep", "speech", "shift", "hum", "raw"], required=True)
    ap.add_argument("--segments", default="", help="hum: label:start-end,... in recording seconds")
    ap.add_argument("--factor", type=int, default=10)
    a = ap.parse_args()

    csv = Path(a.csv)
    out = csv.with_suffix("")
    sensors, marks = load(csv)
    results = {}
    for kind, name in (("a", "accelerometer"), ("g", "gyroscope")):
        t, xyz = sensors[kind]
        tg, xyz, fs = uniform(t, xyz)
        sig, play, snr = best_axis(tg, xyz, marks)
        results[kind] = (name, tg, sig, play, fs, snr)
        print(f"{name:13s} {fs:6.1f} Hz   playback vs silence: {snr:5.1f} dB")

    if a.kind == "sweep":
        fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        for ax, (name, tg, sig, play, fs, snr) in zip(axes, results.values()):
            spec(ax, sig, fs, f"{name} during 20-400 Hz sweep  (SNR {snr:.1f} dB)", fs / 2)
            for m in marks.values():
                ax.axvline(m, color="cyan", lw=0.8, ls="--")
        axes[-1].set_xlabel("seconds  (above 208 Hz the sweep folds back down: aliasing)")

    elif a.kind == "speech":
        ref_path = ROOT / "audio" / f"speech_ref{a.factor}.wav"
        fs_ref, ref = wavfile.read(ref_path)
        ref = ref.astype(float) / 32768
        fmax = min(0.5 * 416 * a.factor, OUT_FS / 2)
        fig, axes = plt.subplots(3, 1, figsize=(10, 8))
        spec(axes[0], ref, fs_ref, "original sentence (low-passed to what the sensor can carry)", fmax)
        for ax, (kind, (name, tg, sig, play, fs, snr)) in zip(axes[1:], results.items()):
            y = to_wav(sig[play], fs, f"{out}_{name}.wav", speedup=a.factor)
            spec(ax, y, OUT_FS, f"recovered from {name}, sped back up {a.factor}x  (SNR {snr:.1f} dB)", fmax)
            print(f"wrote {out}_{name}.wav")
        axes[-1].set_xlabel("seconds")

    elif a.kind == "shift":
        meta = json.loads((ROOT / "audio" / f"speech_{csv.stem}.json").read_text())
        a.factor = meta["factor"]
        fs_ref, ref = wavfile.read(ROOT / "audio" / f"shift_ref{a.factor}.wav")
        ref = ref.astype(float) / 32768
        s_lo, s_hi = meta["speech_band_hz"]
        fmax = 2100
        fig, axes = plt.subplots(3, 1, figsize=(10, 8))
        spec(axes[0], ref, fs_ref, f"original sentence ({s_lo}-{s_hi} Hz)", fmax)
        for ax, kind in zip(axes[1:], ("a", "g")):
            name = results[kind][0]
            t, xyz = sensors[kind]
            tg, xyz, fs = uniform(t, xyz)
            band, mirrored = sensor_band(meta["play_band_hz"], fs)
            axis, sig, play, lead, snr = best_band_axis(tg, xyz, marks, band, fs)
            clean = denoise(sig, sig[lead], fs)
            slow = unshift(clean[play], fs, meta["shift_hz"], mirrored)
            slow = sosfiltfilt(butter(6, [s_lo / a.factor, s_hi / a.factor], "bandpass",
                                      fs=fs, output="sos"), slow)
            y = speed_up(slow, fs, a.factor)
            if "compand" in meta:      # undo companding, then pre-emphasis (reverse of make_audio)
                y = y * compand_gain(y, OUT_FS, meta["compand"], expand=True)
                y = lfilter([1], [1, -meta["preemph"]], y)
                y = sosfiltfilt(butter(6, [s_lo, s_hi], "bandpass", fs=OUT_FS, output="sos"), y)
            y = write_wav(y, f"{out}_{name}.wav")
            gap, match = score(ref, y, (s_lo, s_hi))
            print(f"{name:13s} sensor band {band[0]:.0f}-{band[1]:.0f} Hz"
                  f"{' (mirrored)' if mirrored else ''}, axis {axis}, in-band SNR {snr:.1f} dB"
                  f"  ->  pause gap {gap:.1f} dB, match {match:.3f}")
            print(f"wrote {out}_{name}.wav")
            spec(ax, y, OUT_FS, f"recovered from {name} (axis {axis}, in-band SNR {snr:.1f} dB,"
                                f" pause gap {gap:.0f} dB, match {match:.2f})", fmax)
        axes[-1].set_xlabel("seconds")

    elif a.kind == "hum":   # real voice through bone: compare voice-band level per segment
        segs = [(lab, *map(float, rng.split("-")))
                for lab, rng in (x.split(":") for x in a.segments.split(","))]
        fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        for ax, kind in zip(axes, ("a", "g")):
            name = results[kind][0]
            t, xyz = sensors[kind]
            tg, xyz, fs = uniform(t, xyz)
            voice = sosfiltfilt(butter(4, [70, min(195, 0.49 * fs)], "bandpass", fs=fs, output="sos"),
                                xyz, axis=0)
            mask = lambda lab: np.any([(tg >= s0) & (tg < s1) for l, s0, s1 in segs if l == lab], 0)
            quiet = mask("quiet")
            loud = ~quiet & np.any([(tg >= s0) & (tg < s1) for _, s0, s1 in segs], 0)
            gain = [10 * np.log10(np.mean(voice[loud, i] ** 2) / np.mean(voice[quiet, i] ** 2))
                    for i in range(3)]
            i = int(np.argmax(gain))
            levels = {lab: 10 * np.log10(np.mean(voice[mask(lab), i] ** 2) / np.mean(voice[quiet, i] ** 2))
                      for lab in dict.fromkeys(l for l, _, _ in segs) if lab != "quiet"}
            print(f"{name:13s} axis {'xyz'[i]}, 70-195 Hz level vs quiet: "
                  + ", ".join(f"{k} {v:+.1f} dB" for k, v in levels.items()))
            spec(ax, xyz[:, i], fs, f"{name}, axis {'xyz'[i]}: "
                 + ", ".join(f"{k} {v:+.1f} dB" for k, v in levels.items()), fs / 2, nperseg=128)
            for lab, s0, s1 in segs:
                ax.axvspan(s0, s1, color="cyan", alpha=0.08)
                ax.text((s0 + s1) / 2, fs / 2 * 0.92, lab, color="cyan", ha="center", fontsize=9)
            to_wav(voice[:, i], fs, f"{out}_{name}.wav")
        axes[-1].set_xlabel("seconds")

    else:  # raw: no playback, e.g. humming with the phone pressed to the jaw
        fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        for ax, (name, tg, sig, play, fs, snr) in zip(axes, results.values()):
            spec(ax, sig, fs, f"{name} (raw, real time)", fs / 2)
            to_wav(sig, fs, f"{out}_{name}.wav")
        axes[-1].set_xlabel("seconds")

    fig.tight_layout()
    fig.savefig(f"{out}.png", dpi=110)
    print(f"wrote {out}.png")


if __name__ == "__main__":
    main()
