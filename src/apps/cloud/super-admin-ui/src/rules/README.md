# rules — 콘솔 규칙 엔진 (ALPHA-738)

콘솔 홈을 하드코딩 카드에서 **규칙이 사실 위에서 돌아 나온 결과의 렌더링**으로 교체하는 모듈.
규칙 R01~R16 + 인과 간선 7개 + `evaluate()`가 전부 여기 살고, UI(`pages/ConsolePage.tsx`)는 결과만 받는다.

## 구성

| 파일 | 역할 |
|---|---|
| `types.ts` | Facts(사실)·Violation·Incident·리포트 타입 |
| `rules.ts` | RULES 16종(선언 데이터 + 조건 함수) · EDGES 7개 |
| `evaluate.ts` | 위반 수집 → 인과 병합(사건) → 정렬 · 리뷰 계약 §5 JSON(`buildReport`) |
| `facts-snapshot.json` | 사실 팩(2026-08-03 스냅샷, 목 포함 — mock 플래그로 구분) |
| `cli.ts` | `pnpm eval:rules` — UI 없이 §5 JSON 산출 |
| `rules.test.ts` | `pnpm test:rules` — 규칙당 위반/비위반 픽스처 + 경계 케이스 (node:test) |

Node 직접 실행(cli·test)을 위해 모듈 내부 import 는 `.ts` 확장자를 쓴다(tsconfig `allowImportingTsExtensions`).

## 명세와 다르게 구현한 지점 (구현 노트)

1. **명세 원문 부재** — `edge-console-rules.md`·`edge-console-datapack-v2.json` 파일이 로컬에 없어,
   레퍼런스 `edgeconsolev4.html`(~/Downloads)의 `RULES`·`EDGES`·`evaluate()`·내장 데이터팩을 추출해 SSOT 로 썼다.
   회귀 기준: 위반 29 · 사건 20 · P0 5 (스냅샷 회귀 테스트로 고정).
2. **NOW** — 레퍼런스는 벽시계 상수 고정. 여기서는 `snapshotNow()` = `meta.db`(스냅샷 채취 시각)를
   기본값으로 쓴다. 벽시계로 평가하면 같은 스냅샷의 R02 판정이 시간이 지나며 바뀌어 재현이 깨진다.
3. **evaluated:false 판정** — 명세 §5가 요구하는 "못 돈 규칙 ≠ 조용한 규칙" 구분을 `canRun` 으로 구현:
   R08(actual 근거 전무)·R11(구독 매핑 필드 부재)·R12(큐 축 부재)·R15(per-ETF 원장 부재)·R16(정책 필드 전무).
   현재 스냅샷은 목으로 채워져 있어 전부 evaluated:true + `depends_on_mock` 플래그다 — **MOCK 배지 수 = 남은 계측 부채**.
4. **`absorbed_into` 필드 추가** — §5 예시에 없지만, 흡수된 위반이 리포트에서도 지워지지 않았음을
   기계 검증할 수 있게 위반 항목에 뿌리 사건 키를 붙였다.
5. **실행 위치** — 명세는 "서버 쪽이 자연스럽다" 했으나 UI 워크스페이스의 순수 TS 모듈 + node CLI 로 구현했다.
   사실 소스가 아직 정적 스냅샷(목 포함)이라 서버 이식의 이득이 없고, §5 JSON 은 CLI 로 UI 없이 나온다.
   API 가 사실을 주기 시작하면 이 모듈을 그대로 서버(또는 BFF)로 옮기거나 스냅샷 import 를 fetch 로 바꾼다.
6. **구 홈 잔존** — 이전 `OverviewPage` 는 디자인 검수 비교용으로 `/overview` 에 남겼다(네비 미노출).
   디자인 확정 시 제거 여부를 결정한다.

## 계측 티켓 7건 (요청문 §4 — 발번 대기)

로컬 디자인 검수 후 PR 라운드에서 Jira 발번 예정. 대상: ① AnalyzeOne per-ETF outcome 원장(R15) ·
② 큐→구독 서비스 매핑 선언(R11) · ③ CatalogEntry 재시도 정책 필드(R16) · ④ DatasetContract.actual_as_of
writer(R08, KRX holdings 는 영구 UNKNOWN 이 설계 결정) · ⑤ 완전성 분모 확장 — 엔티티 축만(R07) ·
⑥ task_key 별 런북 등록 · ⑦ 런 kind(정규/수동/백필) 기록.
