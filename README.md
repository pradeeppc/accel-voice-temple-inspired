# accel-voice

Picking up speech with a phone's **accelerometer** instead of its microphone, inspired by
bone-conduction voice wearables, which hear the wearer through skull vibration.

Everything runs on an ordinary Android phone plus a laptop: a tiny recorder app (no Gradle,
no Android Studio) and Python scripts that rebuild audio from the sensor readings.

## Results

Tested on an Oppo CPH2263 (Android 13, ST LSM6DS3C accelerometer). Android exposes it at about
**398 samples/s**, so the sensor can only see vibrations up to about **199 Hz**.

| Experiment | Result |
|---|---|
| Frequency sweep through the phone's own speaker | The speaker only shakes the phone above ~120 Hz. Above 199 Hz the signal folds back down (aliasing). |
| Speech slowed 10x, played through the phone's speaker | Nothing recovered: the slowed speech sits below 120 Hz, where the speaker can't move the phone. |
| Speech slowed 16x **and shifted up to 285-390 Hz** | **Sentence recovered.** The sensor sees it folded to ~10-113 Hz; the analysis undoes the fold. Pre-emphasis plus 2:1 companding (`cshift`) quiets the pauses. |
| Real voice, phone pressed to the head or neck | The voice's pitch (~130 Hz) and each spoken word's timing show clearly, but there are no intelligible words: those need sound above 199 Hz. |

![Sweep](docs/sweep.png)
*20-400 Hz sweep: nothing below ~120 Hz; above 199 Hz the sweep folds back down.*

![Recovered sentence](docs/recovered_sentence.png)
*Top: the original sentence. Middle: rebuilt from the accelerometer (slowed 16x, shifted, companded).*

![Placements](docs/placements.png)
*Humming, then counting 1-10, with the phone pressed to four places. Voice level above quiet
(hum / count): temple +28 / +23 dB, throat +27 / +23 dB, cheekbone +22 / +14 dB, jaw +17 / +15 dB.*

Why the plain 10x slowdown fails: [docs/plain_slowdown.png](docs/plain_slowdown.png).

### Limits

- **Real speech can't be heard from a phone accelerometer.** Words need roughly 1.5-8 kHz of
  bandwidth. Phones cap apps at about 400-500 samples/s, even though the chip itself can run
  in the kHz range. Recovering the sentence only works because the test audio is slowed and shifted
  into the band the phone can see.
- **Single runs.** Each placement was measured once, so differences of a few dB are within
  run-to-run variation.

## How it works

1. **`app/`**: an Android app that records the accelerometer and gyroscope as fast as the
   hardware allows. It requests `HIGH_SAMPLING_RATE_SENSORS` to lift Android 12+'s 200 Hz cap,
   and can play a sound file through the phone's speaker while recording. It has no microphone
   permission. Results are saved as CSV (`sensor, t_ns, x, y, z`).
2. **`make_audio.py`** makes the test sounds: a sweep, slowed speech, and slowed-and-shifted
   speech (single-sideband shift, optional pre-emphasis and companding).
3. **`analyze.py`** puts the sensor timestamps on an even time grid and picks the best axis.
   It then removes the steady background noise, undoes the alias fold and frequency shift,
   speeds the result back up and writes WAV files, spectrograms and scores.
4. **`run.sh`** and **`hum_test.sh`** drive the phone over adb and run the analysis.
   `hum_test.sh` speaks its instructions through the laptop, so you can keep the phone pressed
   to your head.

## Setup

Requirements: Linux, Java 17+ runtime, Python 3 with `numpy scipy pandas matplotlib`,
`ffmpeg` (its built-in flite text-to-speech makes the test sentence), `adb`, `bc`, `paplay`.

Install the Android build tools (you'll be asked to accept the SDK license):

```bash
# Android command-line tools from https://developer.android.com/studio#command-tools,
# unzipped to ~/Android/Sdk/cmdline-tools/latest
~/Android/Sdk/cmdline-tools/latest/bin/android --sdk=$HOME/Android/Sdk sdk install build-tools/35.0.1 platforms/android-34
```

Turn on USB debugging on the phone, connect it, then build and install the app:

```bash
./build.sh install
```

## Usage

Put the phone flat on a table, unlocked, with nothing touching it:

```bash
./run.sh sweep          # 20-400 Hz sweep: how well the speaker shakes the phone at each frequency
./run.sh speech 10      # speech slowed 10x (fails, see Results)
./run.sh shift 16       # speech slowed 16x and shifted into 285-390 Hz
./run.sh cshift 16      # same, plus pre-emphasis and companding
./run.sh raw 10         # record 10 s with no sound playing
```

Real voice through bone (follow the laptop's spoken instructions):

```bash
./hum_test.sh temple    # any placement name: jaw, cheekbone, temple, throat, ...
```

Outputs go to `data/`: the raw CSV, rebuilt WAVs and a spectrogram PNG. Test sounds are
generated into `audio/` on first use.

The speaker band (`PLAY_BAND_LO` in `make_audio.py`) was measured on one phone. Run
`./run.sh sweep` on yours and adjust it if needed.
