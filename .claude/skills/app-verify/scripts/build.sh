#!/usr/bin/env bash
# 검증용 release APK(x86_64, 디버그 서명, JS 번들 포함)를 만든다. 개발 서버 없이 앱만 켜도 돈다.
# 분석 오염 방지: PostHog·Meta 키 없음, Firebase 는 가짜 설정. 실제 Firebase·PostHog 키를 넣지 않는다.
# 사용: [MODE=mock] build.sh [app-ui 경로]   결과: $OUT/etforca-verify.apk (MODE=mock 이면 etforca-verify-mock.apk)
# MODE=mock: 앱 안의 가짜 API(src/api/mock)를 쓴다. 서버에 아무것도 보내지 않고 데이터가 고정이라 디자인 비교(app-design)에 쓴다.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); source "$HERE/env.sh"
APP_DIR=$(cd "${1:-$HERE/../../../../apps/etforca/app-ui}" && pwd)
REPO=$(git -C "$APP_DIR" rev-parse --show-toplevel)
OUT=${OUT:-$MOBILE_ENV/out}; mkdir -p "$OUT"
MODE=${MODE:-http}
# 주의: NAME=$([ ... ] && echo ...) 꼴은 http 일 때 set -e 로 조용히 끝난다(2026-11-25 실제로 겪음)
if [ "$MODE" = mock ]; then NAME=etforca-verify-mock.apk; else NAME=etforca-verify.apk; fi
export EXPO_PUBLIC_API_MODE=$MODE EXPO_PUBLIC_API_URL=https://etforca.edgesignal.dev/api/v1
export EXPO_PUBLIC_POSTHOG_KEY= META_APP_ID= META_CLIENT_TOKEN=
export KAKAO_NATIVE_APP_KEY=$(sed -n "s/^KAKAO_NATIVE_APP_KEY=//p" "$MOBILE_ENV/secrets/kakao.env" 2>/dev/null || true)
export GOOGLE_SERVICES_JSON=$MOBILE_ENV/fake-firebase/google-services.json
export GOOGLE_SERVICE_INFO_PLIST=$MOBILE_ENV/fake-firebase/GoogleService-Info.plist
export CI=1 NODE_ENV=production
FAKE=$REPO/.github/scripts/write_fake_firebase.py
if [ -f "$FAKE" ]; then python3 "$FAKE"; else [ -f "$GOOGLE_SERVICES_JSON" ] || { echo "가짜 Firebase 설정이 없다: $FAKE"; exit 1; }; fi
# Metro 변환 캐시에는 EXPO_PUBLIC_* 값이 박혀 있다. 지우지 않으면 MODE 를 바꿔도 이전 빌드의 API 모드가 남는다(2026-11-25 확인).
rm -rf "$TMPDIR"/metro-cache "$TMPDIR"/metro-file-map-*
cd "$APP_DIR"
[ -d node_modules ] || npm ci --no-audit --no-fund
npx expo prebuild --platform android --clean --no-install
cd android
echo "org.gradle.jvmargs=-Xmx6g -XX:MaxMetaspaceSize=1g" >> gradle.properties
./gradlew assembleRelease -PreactNativeArchitectures=x86_64 --no-daemon
cp app/build/outputs/apk/release/app-release.apk "$OUT/$NAME"
echo "APK: $OUT/$NAME ($(git -C "$APP_DIR" rev-parse --short HEAD)$(git -C "$APP_DIR" diff --quiet HEAD -- . || echo -dirty))" | tee "$OUT/$NAME.commit"
