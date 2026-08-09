# rules — 콘솔 규칙 엔진 (ALPHA-738)

콘솔 홈을 하드코딩 카드에서 **규칙이 사실 위에서 돌아 나온 결과의 렌더링**으로 교체하는 모듈.
규칙 R01~R19 + 인과 간선 7개 + `evaluate()`가 전부 여기 살고, UI(`pages/ops/*`)는 결과만 받는다.

## 화면 구성 (IA)

레퍼런스 HTML 의 상단 탭 6개는 **사이드바의 형제 화면**으로 풀었다 — 콘솔 탭 안에 또 탭을 두지 않는다.

| 경로 | 화면 (답하는 질문) |
|---|---|
| `/` | 오늘 사건 — 오늘 무엇이 깨졌고 조치 단위는 몇 개인가 |
| `/ops/runs` | 런·작업 귀결 |
| `/ops/chain` | 설명 생산 체인 |
| `/ops/datasets` | 데이터셋 신선도 |
| `/ops/trend` | 산출 추이 |
| `/ops/delivery` | 전달 경계 |

사건 카드 클릭은 `?focus=<행 id>` 로 축 화면에 착지해 그 행을 강조한다(L3). 스타일은 ui-kit
토큰·프리미티브만 쓴다 — 자체 팔레트·테마 토글·커스텀 툴팁 레이어를 두지 않고, L2 는 네이티브
`title`(GridPage 와 같은 관용구)이다.

## 구성

| 파일 | 역할 |
|---|---|
| `types.ts` | Facts(사실)·Violation·Incident·리포트 타입 — **위반 필드 규약**(`target`/`targetId`/`metric`/`unit`/`state`/`why`)이 `RawViolation` 주석에 있다. 정본은 거기다 |
| `rules.ts` | RULES 19종(선언 데이터 + 조건 함수) · EDGES 7개 |
| `evaluate.ts` | 위반 수집 → 인과 병합(사건) → 정렬 · 리뷰 계약 §5 JSON(`buildReport`) · 사건 키 조립(`vidOf`)과 **그 역함수**(`ruleOfVid`) — 소비자가 구분자를 다시 적으면 조용히 아무것도 못 찾는다 |
| `facts-snapshot.json` | 사실 팩(2026-08-03 스냅샷, 목 포함 — mock 플래그로 구분). **단계 4에서 `/api/v1/console/facts` fetch 로 교체된다**(ADR-0049 — 서버·검증 경계·어댑터는 섰고, 화면 배선만 남았다) |
| `cli.ts` | `pnpm eval:rules` — UI 없이 §5 JSON 산출 |
| `rules.test.ts` | `pnpm test:rules` — 규칙당 위반/비위반 픽스처 + 경계 케이스 (node:test) |

Node 직접 실행(cli·test)을 위해 모듈 내부 import 는 `.ts` 확장자를 쓴다(tsconfig `allowImportingTsExtensions`).

⚠️ 이 제약은 **이 폴더 밖에도 적용된다.** `pnpm test:rules` 는 `src/**/*.test.ts` 를 돌리므로
`node --test` 가 읽는 모듈이 화면 쪽에도 있다(`pages/ops/notRun.ts`·`consoleFacts.ts` — 규칙
결과를 읽는 순수 판단들). **JSX 를 쓰는 파일에 그런 판단을 두면 node 가 import 자체를 못 해
변이가 하나도 안 잡힌다** — 그래서 판단은 `.ts` 로 내리고 import 에 확장자를 붙인다.

## 명세와 다르게 구현한 지점 (구현 노트)

1. **명세 원문 부재** — `edge-console-rules.md`·`edge-console-datapack-v2.json` 파일이 로컬에 없어,
   레퍼런스 `edgeconsolev4.html`(~/Downloads)의 `RULES`·`EDGES`·`evaluate()`·내장 데이터팩을 추출해 SSOT 로 썼다.
   회귀 기준: 위반 29 · 사건 20 · **P0 6** (스냅샷 회귀 테스트로 고정).
   ⚠️ P0 는 레퍼런스의 5 에서 **6 으로 옮겼다**(ALPHA-738 B2a) — R14 가 비게시 발번을 `seed_note`
   만 보고 P2 로 강등하던 것을 없앴다. 그 값은 합계라 "전량 시드"를 가정할 수 없고, 실 응답에서
   그 수는 "무효화 통지가 안 간 발번"이라 진짜 P0 다. 회귀가 아니라 정정이다.
2. **NOW** — 레퍼런스는 벽시계 상수 고정. 여기서는 `snapshotNow()` = `meta.db`(스냅샷 채취 시각)를
   기본값으로 쓴다. 벽시계로 평가하면 같은 스냅샷의 R02 판정이 시간이 지나며 바뀌어 재현이 깨진다.
3. **evaluated:false 판정** — 명세 §5가 요구하는 "못 돈 규칙 ≠ 조용한 규칙" 구분. 생산자는 **둘**이고
   `RuleResult.notRun` 이 어느 쪽인지 구조로 낸다:
   - `notRun: 'axis'` — `canRun` 이 거짓(읽을 사실 축이 없다): R08(actual 근거 전무)·R11(구독 매핑
     필드 부재)·R12(큐 축 부재)·R15(per-ETF 원장 부재)·R16(정책 필드 전무)·R17~R19(minute 축 부재).
   - `notRun: 'identity'` — 축은 있었는데 **응답이 사건을 못 가르게** 줬다(사건 식별자 충돌·빈 대상/범위
     축). `canRun` 과 무관하게 `evaluate` 가 그 규칙의 위반을 통째로 버리고 세운다. 계측 공백이 아니라
     계약 위반이라 화면도 다른 칸에 그린다(`pages/ops/notRun.ts`).

   그래서 `canRun` 목록을 "evaluated:false 인 규칙의 전부"로 읽으면 안 된다.
   동봉 스냅샷 기준 실측: **`evaluated:false` 는 R17~R19 셋**이다 — 스냅샷은 배치 원장만 담고
   실시간 세션 축은 화면이 `/sources/minute` 응답을 실어 줄 때만 채워진다. 나머지는 evaluated:true
   이고, 그중 목으로 채워진 것이 `depends_on_mock`(R07·R08·R11·R15·R16 — R12 는 evaluated:true
   지만 목이 아니고, R07 은 canRun 이 없지만 목이다) — **MOCK 배지 수 = 남은 계측 부채**.
4. **§5 예시에 없는 필드** — 전부 "명세가 요구한 성질을 기계 검증할 수 있게" 붙인 것이다.
   - `violations[].absorbed_into` — 흡수된 위반이 리포트에서도 지워지지 않았음을 뿌리 사건 키로 증명.
   - `violations[].vid`·`incidents[].members[].vid` — **사건 키 축**(`${rule}:${targetId}[@scope]`).
     `rule`+`target_id` 만으로는 같은 작업이 두 런에 걸린 날 멤버 두 줄이 글자 하나 안 틀리게 같아져
     조인이 끊긴다.
   - `violations[].scope` — 그 vid 의 시점 범위. 소비자가 `scope ?? run_id` 를 다시 조립하지 않게.
   - `rules[].notRun` — 위 3의 두 종류(`axis`·`identity`).
5. **실행 위치** — 명세는 "서버 쪽이 자연스럽다" 했으나 UI 워크스페이스의 순수 TS 모듈 + node CLI 로 구현했다.
   사실 소스가 아직 정적 스냅샷(목 포함)이라 서버 이식의 이득이 없고, §5 JSON 은 CLI 로 UI 없이 나온다.
   **이 선택은 닫혔다** — 엔드포인트가 사실만 주고 평가는 여기 남는다
   ([ADR-0049](../../../../../../docs/adr/0049-console-facts-endpoint.md), 계약은
   [docs/contracts/console-facts-api.md](../../../../../../docs/contracts/console-facts-api.md)).
6. **구 홈 잔존** — 이전 `OverviewPage`(실데이터 레인 요약)는 `/overview` 에 "레인 원장 요약"으로 남겼다.
   규칙 엔진은 정적 스냅샷 위에서 돌고 저 화면은 실 API 를 읽는다 — 축이 달라 대체가 아니라 병존이다.
7. **`max_retries: 0` = 정책 미선언** — 원장은 재시도 정책이 없음을 `0` 으로 적는다(SFN Retry 블록 0개,
   27개 중 17개). 이를 "상한 0회"로 읽으면 화면이 `1/0` 이라는 없는 분모를 그리고 R16 이 "평가됨"이라
   주장한다. `retryCap()` 에서 한 번만 정규화하고, 상한이 없으면 화면도 "시도 N회 · 상한 미선언"까지만 쓴다.
   레퍼런스 HTML 은 falsy 검사(`t.max_retries &&`)로 우연히 같은 결과를 냈지만 의도가 코드에 없었다.

## 계측 티켓 7건 (요청문 §4 — 발번 대기)

로컬 디자인 검수 후 PR 라운드에서 Jira 발번 예정. 대상: ① AnalyzeOne per-ETF outcome 원장(R15) ·
② 큐→구독 서비스 매핑 선언(R11) · ③ CatalogEntry 재시도 정책 필드(R16) · ④ DatasetContract.actual_as_of
writer(R08, KRX holdings 는 영구 UNKNOWN 이 설계 결정) · ⑤ 완전성 분모 확장 — 엔티티 축만(R07) ·
⑥ task_key 별 런북 등록 · ⑦ 런 kind(정규/수동/백필) 기록.
