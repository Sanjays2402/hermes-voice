#!/usr/bin/env bash
# Build Hermes Screen (an APK for the Echo Show) without Gradle: aapt2, javac, d8, zipalign, apksigner.
# Needs a JDK (17) and the Android SDK with build-tools and platforms;android-25:
#   brew install openjdk@17 && brew install --cask android-commandlinetools
#   sdkmanager --licenses && sdkmanager "build-tools;34.0.0" "platforms;android-25"
# Output: build/hermes-screen.apk, signed with a key made on first build (build/screen.keystore, not committed:
# keep it to install updates over the installed app).
set -euo pipefail
cd "$(dirname "$0")"
SDK=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-/opt/homebrew/share/android-commandlinetools}}
BT=$SDK/build-tools/${BUILD_TOOLS:-34.0.0}
JAR=$SDK/platforms/android-25/android.jar
JAVA_HOME=${JAVA_HOME:-/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home}
export PATH="$JAVA_HOME/bin:$PATH"
for f in "$BT/aapt2" "$BT/d8" "$BT/zipalign" "$BT/apksigner" "$JAR"; do
  [ -e "$f" ] || { echo "missing $f (see the comment at the top of build.sh)" >&2; exit 1; }
done
rm -rf build/obj build/dex build/*.unsigned.apk build/*.aligned.apk
mkdir -p build/obj build/dex
"$BT/aapt2" link -o build/app.unsigned.apk -I "$JAR" --manifest AndroidManifest.xml \
  --min-sdk-version 22 --target-sdk-version 25
javac --release 8 -classpath "$JAR" -d build/obj $(find src -name '*.java') 2>&1 | grep -v "bootstrap classpath" || true
"$BT/d8" --min-api 22 --lib "$JAR" --output build/dex $(find build/obj -name '*.class')
(cd build/dex && zip -q ../app.unsigned.apk classes.dex)
"$BT/zipalign" -f 4 build/app.unsigned.apk build/app.aligned.apk
if [ ! -f build/screen.keystore ]; then
  keytool -genkeypair -keystore build/screen.keystore -storepass hermesscreen -keypass hermesscreen \
    -alias screen -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Hermes Screen" >/dev/null 2>&1
fi
"$BT/apksigner" sign --ks build/screen.keystore --ks-pass pass:hermesscreen --key-pass pass:hermesscreen \
  --out build/hermes-screen.apk build/app.aligned.apk
"$BT/apksigner" verify build/hermes-screen.apk
rm -f build/app.unsigned.apk build/app.aligned.apk
echo "built $(pwd)/build/hermes-screen.apk ($(wc -c < build/hermes-screen.apk | tr -d ' ') bytes)"
