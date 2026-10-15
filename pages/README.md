# pages — 아키텍처 문서 포털 (MkDocs Material)

빌드 파이프라인은 [deploy-pages.yml](../.github/workflows/deploy-pages.yml)이 정본이다.
로컬에서 빌드·미리보기할 때는 **sync 를 먼저** 돌려야 한다 — reference 문서와
브랜드 로고·파비콘(캐노니컬 `src/libs/ui-kit/src/assets`, ALPHA-950·952)은 커밋되지 않고
sync 가 복사해 넣는 빌드 산출물이라, 건너뛰면 로고·reference 링크가 깨진다.
헤더 로고는 sync 가 캐노니컬 로고에 Pages 전용 모션 레이어를 더해 생성하는
`edge-logo-animated.svg` 다(ALPHA-971) — 마찬가지로 커밋되지 않는다.

```bash
# repo root 에서 (시스템 python 은 PEP 668 로 직설치가 막히므로 venv 사용)
python3 -m venv .venv && source .venv/bin/activate
pip install "mkdocs-material==9.7.6"
python pages/scripts/sync_reference_docs.py
python pages/scripts/generate_evolution.py
mkdocs build -f pages/mkdocs.yml --strict   # 또는 mkdocs serve -f pages/mkdocs.yml
```

다이어그램 5(데이터 파이프라인)의 뷰어 HTML(`interactive/5-dp.v1~v5.html`)은 커밋돼 있고, 원본 drawio(레포 밖,
판 v0.3·v0.4·v2.0·v2.5·v3.1 = 단계 v1~v5)가 바뀌면
`python pages/scripts/build_pipeline_diagram.py <원본.drawio> pages/docs/diagrams/interactive/5-dp.v<N>.html --title "…"`
로 다시 만든다 — draw.io 데스크톱 CLI 가 필요하다(`DRAWIO_BIN`).
