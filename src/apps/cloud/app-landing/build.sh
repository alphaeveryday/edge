#!/usr/bin/env bash
# landing.html 은 Claude Artifact용 조각 문서다(doctype·<html>·viewport 메타가 없다 — Artifact 가
# 게시할 때 감싼다). 여기서 문서 머리를 붙여 dist/ 를 만든다. 배포 워크플로와 로컬 확인이 이
# 스크립트를 같이 쓴다.
set -euo pipefail
cd "$(dirname "$0")"

# 완성된 문서가 들어오면 머리가 두 번 붙는다. 깨진 페이지를 올리지 않게 여기서 멈춘다.
if grep -qi '<!doctype' landing.html; then
  echo "landing.html 에 doctype 이 있다. 조각 문서로 되돌리거나 build.sh 의 머리 붙이기를 없앤다." >&2
  exit 1
fi

rm -rf dist
mkdir dist
{
  printf '%s\n' '<!doctype html>' '<html lang="ko">' '<meta charset="utf-8">' \
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
  cat landing.html
} > dist/index.html
cp -R assets dist/assets
# 검색엔진이 사이트 최상위에서 찾는 파일.
cp robots.txt sitemap.xml dist/
