#!/usr/bin/env bash
# Bone-conduction test with your real voice: the phone records its sensors (no sound playing)
# while the laptop speaks the steps out loud.
#   ./hum_test.sh [PLACE]     PLACE = jaw (default), cheekbone, ... -> results in data/hum_PLACE.*
#   quiet 0-6 s  |  hum 6-14 s  |  count 14-24 s  |  quiet 24-30 s
set -euo pipefail
cd "$(dirname "$0")"

PKG=com.accelvoice
DIR=/sdcard/Android/data/$PKG/files
PLACE=${1:-jaw}
TAG=hum_$PLACE
CUES=audio/cues
mkdir -p "$CUES" data

say() {  # speak a cue on the laptop (flite voice via ffmpeg), cached as a wav
    local name=$1 text=$2 f="$CUES/$1.wav"
    if [ ! -f "$f" ]; then
        printf '%s' "$text" > "$CUES/$name.txt"
        ffmpeg -hide_banner -loglevel error -y -f lavfi -i "flite=textfile=$CUES/$name.txt:voice=slt" "$f"
    fi
    paplay "$f"
}

adb shell input keyevent KEYCODE_WAKEUP
say "intro_$PLACE" "Hum test. Press the phone firmly against your $PLACE, and keep it still. Starting in five seconds."
sleep 5
adb shell rm -f "$DIR/$TAG.done"
adb shell am start -S -n "$PKG/.MainActivity" --es tag "$TAG" --ei dur_ms 30000 >/dev/null
start=$(date +%s.%N)
at() { sleep "$(echo "$1 - ($(date +%s.%N) - $start)" | bc)"; }

say quiet "Stay quiet."
at 6;  say hum "Now hum, low and steady."
at 14; say count "Now count slowly from one to ten."
at 24; say stop "Stop. Stay quiet."

until adb shell test -f "$DIR/$TAG.done"; do sleep 1; done
say done "Done. You can put the phone down."
adb shell cat "$DIR/$TAG.done"
adb pull "$DIR/$TAG.csv" "data/$TAG.csv" >/dev/null
python3 analyze.py "data/$TAG.csv" --kind hum --segments "quiet:1.5-6,hum:8-14,count:16.5-24,quiet:26-29.5"
