#!/usr/bin/env bash
# landing.html 은 Claude Artifact용 조각 문서다(doctype·<html>·<head>·<body>·viewport 메타가 없다 — Artifact 가
# 게시할 때 감싼다). 여기서 문서 뼈대를 씌워 dist/ 를 만든다. 배포 워크플로와 로컬 확인이 이
# 스크립트를 같이 쓴다.
set -euo pipefail
cd "$(dirname "$0")"

# 완성된 문서가 들어오면 뼈대가 두 번 씌워진다. 깨진 페이지를 올리지 않게 여기서 멈춘다.
if grep -qi '<!doctype' landing.html; then
  echo "landing.html 에 doctype 이 있다. 조각 문서로 되돌리거나 build.sh 의 뼈대 씌우기를 없앤다." >&2
  exit 1
fi

# 머리와 본문의 경계 줄. 브라우저는 <head> 가 없어도 머리를 추론하지만, <head> 없이 서빙했을 때 구글 서치 콘솔
# 소유 확인이 "메타 태그가 <head> 섹션에 없습니다"로 실패했다(ALPHA-1264). 그래서 경계를 기준으로 <head>·<body> 를 명시한다.
# 경계가 없거나 둘 이상이면 어디서 나눌지 알 수 없으므로 멈춘다.
MARK='<!-- build:body -->'
if [ "$(grep -cxF -- "$MARK" landing.html)" != 1 ]; then
  echo "landing.html 에 '$MARK' 줄이 정확히 하나 있어야 한다(머리와 본문의 경계)." >&2
  exit 1
fi

rm -rf dist
mkdir dist
{
  printf '%s\n' '<!doctype html>' '<html lang="ko">' '<head>' '<meta charset="utf-8">' \
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
  sed "/^$MARK\$/,\$d" landing.html
  printf '%s\n' '</head>' '<body>'
  sed "1,/^$MARK\$/d" landing.html
  printf '%s\n' '</body>' '</html>'
} > dist/index.html
cp -R assets dist/assets
# 검색엔진이 사이트 최상위에서 찾는 파일.
cp robots.txt sitemap.xml dist/
