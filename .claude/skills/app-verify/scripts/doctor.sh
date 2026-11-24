#!/usr/bin/env bash
# 읽기 전용 점검: 이 인스턴스를 조작해도 되는가. 하나라도 FAIL 이면 run.sh 를 돌리지 않는다.
set -uo pipefail
source "$(dirname "$0")/env.sh"
fail=0
chk() { if eval "$2" >/dev/null 2>&1; then echo "OK   $1"; else echo "FAIL $1"; fail=1; fi; }
chk "adb 서버 상시 실행(mobile-adb)" "systemctl --user is-active -q mobile-adb"
chk "에뮬레이터 유닛(mobile-emulator)" "systemctl --user is-active -q mobile-emulator"
chk "기기 1대 연결(device 상태)" "[ \"\$(adb devices | grep -c 'device\$')\" = 1 ]"
chk "부팅 완료" "[ \"\$(adb shell getprop sys.boot_completed | tr -d '\r')\" = 1 ]"
chk "앱 설치됨($APP_ID)" "adb shell pm path $APP_ID | grep -q package:"
chk "Maestro" "maestro --version"
chk "운영 API 응답(/etfs)" "curl -fsS -m 10 -o /dev/null https://etforca.edgesignal.dev/api/v1/etfs"
[ -f "$MOBILE_ENV/out/installed.commit" ] && echo "INFO $(cat "$MOBILE_ENV/out/installed.commit")"
exit $fail
