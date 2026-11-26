#!/usr/bin/env bash
# 앱 디자인을 design.etforca.com 에 올린다: 웹 빌드(/app/) → 화면 모음 라이트·다크 촬영(/screens/) → S3 업로드 → 캐시 비우기.
# 실행(앱 코드를 고친 뒤): apps/etforca/app-ui/scripts/design-sync/design-sync.sh
#   --local   올리지 않고 만들기만 한다(결과: $DESIGN_SYNC_HOME/out)
# 환경: AWS_PROFILE(기본 work), DESIGN_SYNC_HOME(기본 ~/.cache/design-sync — playwright-core·브라우저·결과물)
# 인프라: infra/terraform/envs/dev/main.tf 의 module.design_site. 비밀번호는 그 KeyValueStore 의 basic_auth 키.
set -euo pipefail

DOMAIN=design.etforca.com
BUCKET=edge-dev-app-design
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$(cd "$HERE/../.." && pwd)"
HOME_DIR="${DESIGN_SYNC_HOME:-$HOME/.cache/design-sync}"
OUT="$HOME_DIR/out"
LOCAL=0
[[ "${1:-}" == "--local" ]] && LOCAL=1
export AWS_PROFILE="${AWS_PROFILE:-work}"

commit="$(git -C "$APP" rev-parse --short HEAD)"
git -C "$APP" diff --quiet HEAD -- . || commit="$commit+수정중"

echo "1/4 웹 빌드 ($commit)"
rm -rf "$OUT" && mkdir -p "$OUT"
(cd "$APP" && DESIGN_SYNC_BASE_URL=/app EXPO_PUBLIC_API_MODE=mock CI=1 npx expo export --platform web --output-dir "$OUT/app" >"$HOME_DIR/export.log" 2>&1) \
  || { tail -20 "$HOME_DIR/export.log"; exit 1; }
grep -q 'src="/app/_expo/' "$OUT/app/index.html" || { echo "빌드가 /app 아래 경로를 쓰지 않아요(app.config.ts 의 baseUrl 확인)"; exit 1; }

echo "2/4 화면 촬영"
if [[ ! -d "$HOME_DIR/node_modules/playwright-core" ]]; then
  npm install --prefix "$HOME_DIR" --no-audit --no-fund --silent playwright-core@1.65.1
fi
node "$HOME_DIR/node_modules/playwright-core/cli.js" install chromium-headless-shell >/dev/null
NODE_PATH="$HOME_DIR/node_modules" node "$HERE/capture.cjs" "$OUT" "$commit"

if (( LOCAL )); then echo "올리지 않음(--local): $OUT"; exit 0; fi

echo "3/4 업로드 → s3://$BUCKET"
# 해시 이름 파일은 오래 캐시, 나머지(html·화면 그림)는 매번 확인
aws s3 sync "$OUT" "s3://$BUCKET" --delete --only-show-errors --exclude 'app/_expo/static/*' --cache-control 'no-cache'
aws s3 sync "$OUT/app/_expo/static" "s3://$BUCKET/app/_expo/static" --delete --only-show-errors --cache-control 'public, max-age=31536000, immutable'

echo "4/4 CloudFront 캐시 비우기"
dist="$(aws cloudfront list-distributions --query "DistributionList.Items[?contains(Aliases.Items, '$DOMAIN')].Id | [0]" --output text)"
[[ -n "$dist" && "$dist" != "None" ]] || { echo "$DOMAIN 배포를 못 찾았어요"; exit 1; }
aws cloudfront create-invalidation --distribution-id "$dist" --paths '/*' --query 'Invalidation.Id' --output text >/dev/null
echo "완료: https://$DOMAIN  (화면 모음 https://$DOMAIN/screens/)"
