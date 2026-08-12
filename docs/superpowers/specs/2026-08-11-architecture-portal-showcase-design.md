# Edge Architecture Portal 쇼케이스 개편 설계

- 날짜: 2026-08-11
- 상태: 설계 확정 대기 (사용자 리뷰 중)
- 배경: PokeClip(poke-clip-architecture.vercel.app)·FillMap(fillmap-docs.vercel.app) 류의
  "허브 홈 + 번호 산출물 인덱스 + 섹션 페이지" IA 를 edge 의 기존 GitHub Pages 포털에 입힌다.

## 목표·비목표

**목표**: 외부 쇼케이스(심사·멘토·채용 독자)용 아키텍처 포털. 첫 화면에서
"무엇을 만들었고 어떤 설계 결정을 했는가"가 번호 인덱스로 한눈에 잡히게 한다.

**비목표**:
- 유스케이스·유저 저니 등 존재하지 않는 제품 기획 산출물의 신규 창작 — 하지 않는다.
  레퍼런스 사이트의 해당 섹션은 제품 포트폴리오라서 있는 것이고, edge 의 소재는
  아키텍처 깊이(하이브리드 온프렘·ADR 50건·발전 이력)다.
- 기존 파이프라인 교체 — deploy-pages.yml·sync_reference_docs.py·strict 빌드·
  edge-pages 퍼블리시 구조는 그대로 둔다. 별도 SPA 신설은 콘텐츠 이중화라 배제.

## 빌드 방식

MkDocs Material 유지 + 홈만 템플릿 오버라이드(`pages/overrides/home.html`).
변경 범위는 `pages/` 내부(mkdocs.yml nav·홈 오버라이드·diagrams 섹션)와 신규 SVG 자산뿐.

## 사이트 IA (내비게이션)

```
Home(/) · Diagrams(/diagrams/…) · 설계·ADR(/reference/adr/) · 계약(/reference/contracts/…)
· Evolution(/evolution/) · Retrospective(/retrospective/) · Reference(기존 유지)
```

(계약·ADR 은 nav 만 1급 승격하고 파일 경로는 sync 산출물인 `reference/` 밑을 유지한다 —
sync 스크립트의 `_COPIED_DIRS` 재타깃을 피하기 위함. 데이터플로우 섹션은 산출물 준비
전이라 홈 인덱스의 "작성 예정" 행으로만 둔다.)

- 계약(sync-protocol 등 5건)·발전 과정·회고를 Reference 하위에서 1급 메뉴로 승격 —
  온프렘 하이브리드 서사와 반복 개선 이력이 edge 쇼케이스의 차별점이므로.
- 나머지 Reference(도메인·콘솔 IA 등)는 현행 유지.

## 홈(허브) 구성

위→아래: ① 히어로(캐치프레이즈 자리 + 한 줄 설명) → ② CTA 2개("다이어그램 보기",
"설계·ADR") → ③ 번호 산출물 인덱스:

```
1~4  다이어그램 (확정 4종 — 2026-08-12 1-base 로 전환, "서비스"는 "애플리케이션"으로 통일)
5·6  다이어그램 (후보 2종 — 채택 시 번호 편입, 미채택 시 이후 번호를 당겨 공번 없이 확정)
7    설계 결정 — ADR 현황판 50건
8    계약 5건 (sync-protocol · event-bundle-schema · sync-auth · publication-api · console-facts-api)
9    데이터플로우
10   발전 과정 (evolution)
11   회고 7건 (sprint 1~7)
```

## 다이어그램 계획

확정 4종 — 기존 `docs/architecture` 문서 4축에 1:1 대응(내용 창작 없음, 문서를 그림으로 전사):

| # | 제목 | 근거 문서 |
|---|---|---|
| 1 | 정보 구조 (Information Architecture) | information-architecture.md |
| 2 | 애플리케이션 아키텍처 (cloud/onprem 하이브리드) | application-architecture.md |
| 3 | 시스템 아키텍처 (파이프라인·팬아웃·스크리닝) | system-architecture.md |
| 4 | 클라우드 아키텍처 (AWS) | cloud-architecture.md |

후보 2종 — 채택 여부는 자산 준비도를 보고 결정(미채택이어도 인덱스 번호제라 후일 추가 용이):

| 후보 | 소재 | 원천 자산 |
|---|---|---|
| 데이터 파이프라인 | 수집→정제→게시 흐름 | docs/data-pipeline 계열 문서 |
| Agent (분석 엔진) | causal attribution·ontology | docs/analysis-engine/**/*.drawio (기존 drawio 정리) |

제작 방식: drawio 로 작도 → SVG export → `pages/docs/diagrams/N.md` 가 SVG 임베드 +
이전/다음 링크. 별도 뷰어 앱 없음. 문서-그림 정합은 docs-sync 점검 대상에 편입.

## 작업 단위 (3 PR)

1. **PR-1** IA 재편: nav 개편 + 커스텀 홈, 다이어그램 자리는 placeholder.
   ADR 현황판은 기존 `docs/adr/README.md` 표(번호·제목·상태, 0050까지 최신)를 그대로 쓴다 —
   구현 시 실사 결과 이미 완비돼 있어 요지 열 추가는 중복이라 하지 않는다.
2. **PR-2** 다이어그램 0·1 (IA·서비스).
3. **PR-3** 다이어그램 2·3 (시스템·클라우드) + 후보 2종 채택 판단.

## 테스트·검증

- `mkdocs build -f pages/mkdocs.yml --strict` 통과(기존 CI 게이트 그대로).
- 홈 오버라이드는 Material 업그레이드에 취약하므로 mkdocs-material 버전 고정(현행 9.7.6)을 유지.
- 퍼블리시 후 edge-pages 실사이트에서 최상위 내비 7항목·인덱스 링크 전수 클릭 확인.
