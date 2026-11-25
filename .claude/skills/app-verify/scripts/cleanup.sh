#!/usr/bin/env bash
# 이 스킬이 띄운 에뮬레이터·adb 유닛만 끈다. 증거($MOBILE_ENV/evidence)와 앱 데이터(AVD)는 지우지 않는다.
set -uo pipefail
source "$(dirname "$0")/env.sh"
systemctl --user stop mobile-emulator 2>/dev/null || true
systemctl --user stop mobile-adb 2>/dev/null || true
echo "STOPPED (evidence kept: $MOBILE_ENV/evidence)"
