#!/usr/bin/env bash
# adb 서버와 창 없는 에뮬레이터를 systemd 유닛으로 띄우고(이미 떠 있으면 그대로) 부팅 완료까지 기다린다.
# adb 서버를 먼저 상시 실행한다: 셸마다 adb 가 새로 뜨고 죽으면 에뮬레이터 연결이 끊겨 Maestro 가
# "device offline" 으로 실패한다(2026-11-25 확인).
set -euo pipefail
source "$(dirname "$0")/env.sh"
if ! systemctl --user is-active -q mobile-adb; then
  adb kill-server 2>/dev/null || true
  systemctl --user reset-failed mobile-adb 2>/dev/null || true
  systemd-run --user --unit=mobile-adb --collect --property=Restart=on-failure --property=RestartSec=3 \
    "$ANDROID_HOME/platform-tools/adb" -a nodaemon server start
  sleep 2
fi
if ! systemctl --user is-active -q mobile-emulator; then
  systemd-run --user --unit=mobile-emulator --collect \
    -E ANDROID_HOME -E ANDROID_SDK_ROOT -E ANDROID_AVD_HOME -E ANDROID_USER_HOME -E JAVA_HOME -E TMPDIR \
    "$ANDROID_HOME/emulator/emulator" -avd "$AVD_NAME" -no-window -no-audio -no-boot-anim -accel on -gpu swiftshader_indirect -no-snapshot-save
fi
timeout 300 adb wait-for-device
until [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ]; do sleep 2; done
APK=${APK:-$MOBILE_ENV/out/etforca-verify.apk}
if [ "${INSTALL:-1}" = 1 ] && [ -f "$APK" ]; then
  adb install -r "$APK" >/dev/null   # -r: 앱 데이터(게스트 기기 ID)를 유지한다
  cp "$APK.commit" "$MOBILE_ENV/out/installed.commit" 2>/dev/null || echo "APK: $APK (unknown)" > "$MOBILE_ENV/out/installed.commit"
  echo "INSTALLED $APK"
fi
echo READY
