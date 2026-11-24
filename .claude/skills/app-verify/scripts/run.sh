#!/usr/bin/env bash
# 흐름(flows/*.yaml)을 돌려 증거(스크린샷·Maestro 로그·logcat·JUnit 결과)를 남긴다.
# 사용: run.sh [흐름 파일...]   기본: flows/ 의 운영 흐름 전체(onboarding·mock 태그 제외).
#   ONBOARDING=1 이면 온보딩 흐름도 돈다(게스트 1명 생성). mock 흐름은 파일을 직접 준다: run.sh flows/mock/tour.yaml
# 증거: $MOBILE_ENV/evidence/<시각>-<커밋>/  (정리 스크립트가 지우지 않는다)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); source "$HERE/env.sh"
python3 "$HERE/check_flows.py" "$HERE/../flows"
"$HERE/doctor.sh"
commit=$(sed -n 's/.*(\(.*\))$/\1/p' "$MOBILE_ENV/out/installed.commit" 2>/dev/null || echo unknown)
EV=$MOBILE_ENV/evidence/$(date +%Y%m%d-%H%M%S)-$commit; mkdir -p "$EV"
args=()
if [ $# -gt 0 ]; then flows=("$@")
else flows=("$HERE/../flows"); [ "${ONBOARDING:-0}" = 1 ] && args+=(--exclude-tags mock) || args+=(--exclude-tags onboarding,mock); fi
adb logcat -c
rc=0
maestro test "${args[@]}" --format junit --output "$EV/report.xml" --test-output-dir "$EV/maestro" "${flows[@]}" 2>&1 | tee "$EV/maestro.txt" || rc=$?
adb logcat -d -v time > "$EV/logcat.txt"
grep -E " (E|F)/(ReactNativeJS|AndroidRuntime)" "$EV/logcat.txt" > "$EV/app-errors.txt" || true
echo "EVIDENCE $EV"
echo "앱 오류 로그 $(wc -l < "$EV/app-errors.txt")줄"
exit $rc
