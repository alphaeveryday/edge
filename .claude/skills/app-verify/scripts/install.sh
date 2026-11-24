#!/usr/bin/env bash
# 1회 설치: JDK 17, Android SDK·에뮬레이터(API 35 x86_64), AVD orca35, Maestro. 전부 MOBILE_ENV 안에 둔다(약 15GB).
set -euo pipefail
source "$(dirname "$0")/env.sh"
MAESTRO_VERSION=${MAESTRO_VERSION:-cli-2.12.1}
cd "$MOBILE_ENV"; mkdir -p dl jdk sdk avd gradle
if [ ! -x jdk/bin/java ]; then
  curl -fL -o dl/jdk17.tar.gz 'https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse'
  tar -xzf dl/jdk17.tar.gz -C jdk --strip-components=1
fi
if [ ! -x sdk/cmdline-tools/latest/bin/sdkmanager ]; then
  curl -fL -o dl/cmdline-tools.zip https://dl.google.com/android/repository/commandlinetools-linux-16111833_latest.zip
  rm -rf dl/ct && mkdir -p dl/ct sdk/cmdline-tools && python3 -c "import zipfile;zipfile.ZipFile('dl/cmdline-tools.zip').extractall('dl/ct')"
  chmod +x dl/ct/cmdline-tools/bin/*
  rm -rf sdk/cmdline-tools/latest && mv dl/ct/cmdline-tools sdk/cmdline-tools/latest
fi
yes | sdkmanager --licenses >/dev/null || true
sdkmanager --install "platform-tools" "emulator" "platforms;android-36" "build-tools;36.0.0" "system-images;android-35;google_apis;x86_64"
avdmanager list avd | grep -q "Name: $AVD_NAME" || echo no | avdmanager create avd -n "$AVD_NAME" -k "system-images;android-35;google_apis;x86_64" -d pixel_7
if [ ! -x maestro/bin/maestro ]; then
  gh release download "$MAESTRO_VERSION" --repo mobile-dev-inc/maestro -p maestro.zip -p checksums_sha256.txt -D dl --clobber
  (cd dl && grep ' maestro.zip$' checksums_sha256.txt | sha256sum -c -)
  unzip -q -o dl/maestro.zip -d "$MOBILE_ENV"
fi
rm -rf dl
echo INSTALL_DONE
