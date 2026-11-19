#!/usr/bin/env bash
# Builds and installs the AccelVoice APK without Gradle: ecj -> d8 -> aapt2 -> zipalign -> apksigner.
set -euo pipefail
cd "$(dirname "$0")"

SDK=${ANDROID_HOME:-$HOME/Android/Sdk}
BT=$(ls -d "$SDK"/build-tools/* | sort -V | tail -1)
JAR=$(ls "$SDK"/platforms/android-*/android.jar | sort -V | tail -1)
OUT=build
rm -rf "$OUT" && mkdir -p "$OUT/classes" "$OUT/dex"

echo "build-tools: $BT"
echo "platform:    $JAR"

# Eclipse Java compiler (a single jar that runs on a plain JRE, so no JDK is needed).
ECJ=tools/ecj.jar
if [ ! -f "$ECJ" ]; then
    mkdir -p tools
    curl -sSLo "$ECJ" https://repo1.maven.org/maven2/org/eclipse/jdt/ecj/3.46.100/ecj-3.46.100.jar
fi

# 1. Compile Java (Eclipse compiler runs on the JRE, so no JDK needed).
#    Java 8 mode + android.jar as the boot classpath, so the laptop's own JDK classes stay out.
java -jar "$ECJ" -nowarn -8 \
    -bootclasspath "$JAR" -d "$OUT/classes" $(find app/src -name '*.java')

# 2. Java bytecode -> Android dex.
"$BT/d8" --min-api 26 --lib "$JAR" --output "$OUT/dex" $(find "$OUT/classes" -name '*.class')

# 3. Package the manifest, then add the dex.
"$BT/aapt2" link -o "$OUT/unsigned.apk" -I "$JAR" --manifest app/AndroidManifest.xml \
    --min-sdk-version 26 --target-sdk-version 34 --version-code 1 --version-name 1.0
(cd "$OUT/dex" && zip -q ../unsigned.apk classes.dex)

# 4. Align and sign with a local debug key.
"$BT/zipalign" -f 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
KS=tools/debug.keystore
[ -f "$KS" ] || keytool -genkeypair -keystore "$KS" -storepass android -keypass android \
    -alias debug -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=AccelVoice Debug" >/dev/null 2>&1
"$BT/apksigner" sign --ks "$KS" --ks-pass pass:android --out "$OUT/accelvoice.apk" "$OUT/aligned.apk"
echo "built $OUT/accelvoice.apk"

if [ "${1:-}" = "install" ]; then
    adb install -r "$OUT/accelvoice.apk"
fi
