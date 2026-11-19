"""Make the test audio that the phone plays through its own speaker.

  sweep.wav            20 -> 400 Hz log sweep: measures how well speaker vibration reaches the sensor
  speech_slow{N}.wav   a spoken sentence slowed N times, so speech lands under the sensor's 208 Hz limit
  speech_ref{N}.wav    the same sentence low-passed to what N-times slowing can carry (best possible result)
  speech_shift{N}.wav  the sentence slowed N times and shifted up into the band where the phone's speaker
                       actually shakes the phone (285-390 Hz, measured with the sweep). The sensor sees that
                       band folded (aliased) down to ~10-113 Hz; analyze.py undoes the fold.
  shift_ref{N}.wav     the 250-1900 Hz sentence that speech_shift{N} carries (best possible result)
  speech_cshift{N}.wav same as speech_shift{N}, plus noise reduction we control at the source:
                       pre-emphasis (boost the weak highs) and 2:1 companding (lift the quiet parts).
                       analyze.py undoes both using only the received signal, which pushes the sensor
                       hiss down by the same amount (the dbx/Dolby tape trick).
"""
import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, chirp, hilbert, lfilter, resample_poly, sosfiltfilt

FS_OUT = 48000      # playback sample rate on the phone
FS_SPEECH = 16000
SENSOR_NYQ = 208    # Hz: half of the phone's 416 Hz accelerometer rate
SPEECH_BAND = (250, 1900)   # Hz of the original speech carried by the shifted mode
PLAY_BAND_LO = 285          # Hz: bottom of the band the phone's speaker shakes the phone well
PREEMPH = 0.9               # first-order pre-emphasis coefficient (~+14 dB tilt over 250-1900 Hz)
COMPAND = {"ratio": 0.5, "smooth_ms": 15, "floor_db": -50}   # 2:1 in dB, envelope time constant
TEXT = "The quick brown fox jumps over the lazy dog. One, two, three, four, five."
AUDIO = Path(__file__).parent / "audio"


def save(path, x, fs):
    x = x / (np.max(np.abs(x)) + 1e-12) * 0.9
    wavfile.write(path, fs, (x * 32767).astype(np.int16))
    print(f"wrote {path}  ({len(x) / fs:.1f} s)")


def fade(x, fs, ms=50):
    n = int(fs * ms / 1000)
    ramp = np.linspace(0, 1, n)
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    return x


def make_sweep(seconds=20.0):
    t = np.arange(int(FS_OUT * seconds)) / FS_OUT
    x = chirp(t, f0=20, t1=seconds, f1=400, method="logarithmic")
    save(AUDIO / "sweep.wav", fade(x, FS_OUT), FS_OUT)


def tts(text, voice="kal16"):
    with tempfile.TemporaryDirectory() as d:
        txt, wav = Path(d) / "t.txt", Path(d) / "t.wav"
        txt.write_text(text)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", f"flite=textfile={txt}:voice={voice}",
                        "-ar", str(FS_SPEECH), "-ac", "1", str(wav)], check=True)
        fs, x = wavfile.read(wav)
    return x.astype(np.float64) / 32768


def make_speech(factor, voice):
    x = tts(TEXT, voice)
    # Keep only what survives N-times slowing: anything above 208*N Hz would land above the
    # sensor's limit after slowing, so remove it first.
    cutoff = 0.9 * SENSOR_NYQ * factor
    if cutoff < FS_SPEECH / 2:
        x = sosfiltfilt(butter(8, cutoff, fs=FS_SPEECH, output="sos"), x)
    save(AUDIO / f"speech_ref{factor}.wav", x.copy(), FS_SPEECH)
    # Slowing N times = playing the same samples at FS_SPEECH/N. Resample that up to 48 kHz.
    slow = resample_poly(x, FS_OUT * factor, FS_SPEECH)
    save(AUDIO / f"speech_slow{factor}.wav", fade(slow, FS_OUT), FS_OUT)


def envelope(x, fs, smooth_ms):
    sos = butter(2, 1 / (2 * np.pi * smooth_ms / 1000), fs=fs, output="sos")
    return np.sqrt(np.maximum(sosfiltfilt(sos, x ** 2), 1e-20))


def compand_gain(x, fs, c, expand=False):
    """2:1 compressor gain, or the matching 1:2 expander gain computed from the signal itself."""
    e = envelope(x, fs, c["smooth_ms"])
    e = np.maximum(e, e.max() * 10 ** (c["floor_db"] / 20)) / e.max()
    return e ** (1 / c["ratio"] - 1) if expand else e ** (c["ratio"] - 1)


def make_shifted(factor, voice, compand=False):
    name = f"{'c' if compand else ''}shift{factor}"
    x = tts(TEXT, voice)
    sos = butter(8, SPEECH_BAND, "bandpass", fs=FS_SPEECH, output="sos")
    x = sosfiltfilt(sos, x)
    save(AUDIO / f"shift_ref{factor}.wav", x.copy(), FS_SPEECH)
    meta = {}
    if compand:
        x = lfilter([1, -PREEMPH], [1], x)
        x = sosfiltfilt(sos, x * compand_gain(x, FS_SPEECH, COMPAND))
        meta = {"preemph": PREEMPH, "compand": COMPAND}
    slow = resample_poly(x, FS_OUT * factor, FS_SPEECH)          # now 250/N .. 1900/N Hz
    shift = PLAY_BAND_LO - SPEECH_BAND[0] / factor
    t = np.arange(len(slow)) / FS_OUT
    played = np.real(hilbert(slow) * np.exp(2j * np.pi * shift * t))   # single-sideband shift up
    save(AUDIO / f"speech_{name}.wav", fade(played, FS_OUT), FS_OUT)
    band = [PLAY_BAND_LO, PLAY_BAND_LO + (SPEECH_BAND[1] - SPEECH_BAND[0]) / factor]
    meta.update(factor=factor, shift_hz=shift, play_band_hz=band, speech_band_hz=SPEECH_BAND)
    (AUDIO / f"speech_{name}.json").write_text(json.dumps(meta, indent=2))
    print(f"  plays in {band[0]:.0f}-{band[1]:.0f} Hz")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--factor", type=int, nargs="*", default=[10])
    ap.add_argument("--shift", type=int, nargs="*", default=[], help="slow-down factors for shifted mode")
    ap.add_argument("--cshift", type=int, nargs="*", default=[], help="shifted mode with companding")
    ap.add_argument("--voice", default="kal16")
    a = ap.parse_args()
    AUDIO.mkdir(exist_ok=True)
    make_sweep()
    for n in a.factor:
        make_speech(n, a.voice)
    for n in a.shift:
        make_shifted(n, a.voice)
    for n in a.cshift:
        make_shifted(n, a.voice, compand=True)
