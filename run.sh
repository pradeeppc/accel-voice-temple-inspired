#!/usr/bin/env bash
# Run one experiment on the phone and analyze it.
#   ./run.sh sweep            play a 20-400 Hz sweep, see how much reaches the sensor
#   ./run.sh speech [N]       play the sentence slowed N times (default 10), rebuild it
#   ./run.sh shift [N]        slowed N times (default 16) AND shifted into 285-390 Hz, rebuild it
#   ./run.sh cshift [N]       same as shift, plus pre-emphasis + companding noise reduction
#   ./run.sh raw [SECONDS]    record with no playback (e.g. hum with the phone on your jaw)
set -euo pipefail
cd "$(dirname "$0")"

PKG=com.accelvoice
DIR=/sdcard/Android/data/$PKG/files
mode=${1:?usage: ./run.sh sweep|speech [N]|raw [SECONDS]}

case $mode in
  sweep)  tag=sweep;           audio=sweep.wav ;;
  speech) n=${2:-10}; tag=speech$n; audio=speech_slow$n.wav
          [ -f audio/$audio ] || python3 make_audio.py --factor "$n" ;;
  shift)  n=${2:-16}; tag=shift$n; audio=speech_shift$n.wav
          [ -f audio/$audio ] || python3 make_audio.py --factor --shift "$n" ;;
  cshift) n=${2:-16}; tag=cshift$n; audio=speech_cshift$n.wav; mode=shift
          [ -f audio/$audio ] || python3 make_audio.py --factor --cshift "$n" ;;
  raw)    secs=${2:-10}; tag=raw$secs; audio= ;;
  *) echo "unknown mode $mode"; exit 1 ;;
esac
[ -f audio/sweep.wav ] || python3 make_audio.py

adb shell input keyevent KEYCODE_WAKEUP
adb shell mkdir -p "$DIR"
[ -n "$audio" ] && adb push "audio/$audio" "$DIR/$audio"

extra=(--es tag "$tag")
if [ -n "$audio" ]; then extra+=(--es audio "$audio"); else extra+=(--ei dur_ms $((secs * 1000))); fi
adb shell am start -S -W -n "$PKG/.MainActivity" "${extra[@]}" >/dev/null
echo "recording $tag on the phone ... (keep it still)"

until adb shell test -f "$DIR/$tag.done"; do sleep 1; done
adb shell cat "$DIR/$tag.done"
mkdir -p data
adb pull "$DIR/$tag.csv" "data/$tag.csv"

kind=$mode
python3 analyze.py "data/$tag.csv" --kind "$kind" ${n:+--factor "$n"}
