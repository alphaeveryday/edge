#!/usr/bin/env bash
# 흐름(flows/*.yaml)을 돌려 증거(스크린샷·Maestro 로그·logcat·JUnit 결과)를 남긴다.
# 사용: run.sh [흐름 파일...]   기본: flows/ 의 운영 흐름 전체(onboarding·mock 태그 제외).
#   mock 흐름은 파일을 직접 준다: run.sh .claude/skills/app-verify/flows/mock/tour.yaml
#   온보딩 흐름(게스트 1명 생성)은 ONBOARDING=1 일 때만 돈다.
# 흐름은 반드시 flows/ 안에 있어야 하고, 실제로 돌릴 경로를 그대로 check_flows.py 로 먼저 검사한다.
# 증거: $MOBILE_ENV/evidence/<시각>-<커밋>/  (cleanup.sh 가 지우지 않는다)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); source "$HERE/env.sh"
ROOT=$(cd "$HERE/../flows" && pwd)
args=()
if [ $# -gt 0 ]; then
  flows=("$@")
  for f in "${flows[@]}"; do
    [ -d "$f" ] && { echo "디렉터리 대신 흐름 파일을 준다(기본 실행은 인자 없이): $f"; exit 1; }
    case "$(cd "$(dirname "$f")" && pwd)/$(basename "$f")" in
      "$ROOT/onboarding.yaml") [ "${ONBOARDING:-0}" = 1 ] || { echo "온보딩 흐름은 운영에 게스트를 만든다. ONBOARDING=1 로 명시한다"; exit 1; } ;;
    esac
  done
else
  flows=("$ROOT"); [ "${ONBOARDING:-0}" = 1 ] && args+=(--exclude-tags mock) || args+=(--exclude-tags onboarding,mock)
fi
python3 "$HERE/check_flows.py" "$ROOT" "${flows[@]}"
"$HERE/doctor.sh"
commit=$(sed -n 's/.*(\(.*\))$/\1/p' "$MOBILE_ENV/out/installed.commit" 2>/dev/null || true)
EV=$MOBILE_ENV/evidence/$(date +%Y%m%d-%H%M%S)-${commit:-unknown}; mkdir -p "$EV"
adb logcat -c
rc=0
maestro test "${args[@]}" --format junit --output "$EV/report.xml" --test-output-dir "$EV/maestro" "${flows[@]}" 2>&1 | tee "$EV/maestro.txt" || rc=$?
adb logcat -d -v time > "$EV/logcat.txt"
grep -E " (E|F)/(ReactNativeJS|AndroidRuntime)" "$EV/logcat.txt" > "$EV/app-errors.txt" || true
echo "EVIDENCE $EV"
echo "앱 오류 로그 $(wc -l < "$EV/app-errors.txt")줄"
exit $rc
