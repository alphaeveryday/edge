# Super Admin Console — facts API

> 슈퍼 어드민 콘솔의 규칙 엔진(`super-admin-ui/src/rules/`)이 읽는 **사실 계약**이다.
> 판정 규칙 자체는 이 문서 밖이다 — 룰은 순수 함수이고 이 계약은 그 입력만 정한다.
>
> 배치 결정의 근거는 [ADR-0049](../adr/0049-console-facts-endpoint.md).
> 지금 엔진 입력은 앱 번들 안의 정적 `facts-snapshot.json` 이고, 이 문서는 그 자리를 실 응답으로
> 바꾸는 기준이다(ALPHA-738).

## 엔드포인트

```
GET /api/v1/console/facts?date=YYYY-MM-DD     # date 생략 = 최신 거래일
```

봉투는 기존 콘솔 API 와 같은 `ApiResponse.onSuccess`. 응답은 **사실**이고 위반 목록이 아니다.

`/api/v1/sources/*` 아래 두지 않는다 — 이 응답은 수집 축을 넘는다(전달 경계·산출·큐).

**실시간 축은 이 응답에 없다.** `GET /api/v1/sources/minute` 가 계속 준다 — 화면이 둘을 합친다.

## 부재를 싣는 규약

콘솔은 부재를 네 가지로 가른다(`0` 실측 0 / `—` 집계 없음 / `관측 불가` / `계측 없음`). 와이어는:

| 뜻 | 와이어 |
|---|---|
| 실측 0 | `0` |
| 집계 없음 · 관측 불가 | `null` + 그 축의 `*Unavailable` 사유 |
| 계측 없음 (기록 자체가 없다) | **필드를 안 보낸다** |

**`[]`·`0`·`""` 으로 메우지 않는다** — 화면이 "괜찮다"로 그린다. 표시 문자열도 만들지 않는다
(포맷은 UI 소관 — `SourceReportResponse` 가 이미 지키는 규약).

⚠️ **"안 보낸다"를 적용할 수 있는 축은 현재 `queues`·`etf_ledger`·`minute` 뿐이다.** 나머지는
엔진 타입에서 필수라 빼면 평가가 죽는다. 그 축들의 옵셔널화는 이 계약을 배선하는 작업에 포함된다.

### AWS 제어면은 같은 응답에, 자기 부재 사유와 함께

```jsonc
{
  "queues": null,                                  // []가 아니다 — []는 "큐가 0개"라는 거짓말이다
  "awsUnavailable": "sqs:GetQueueAttributes AccessDenied",
  "meta": { "aws": null, "awsUnobservedRuns": 3 }  // 관측 시각 없음 + 부분 실패의 크기
}
```

축이 `null` 이면 그 축에 의존하는 규칙은 **"위반 0건"이 아니라 `못 돎`** 이 된다.
`awsUnobservedRuns` 는 **부분** 실패용이다 — 런 단위로 붙는 `aws_status` 는 축 단위 부재로
표현되지 않아, 6런 중 5런만 조회된 응답이 완전한 판정처럼 보인다.

## 축별 소스

| 축 | 소스 | 상태 |
|---|---|---|
| `runs[].id·lane·trading_date` | `ops_pipeline_run.run_key·pipeline_type·trading_date` | ✅ |
| `runs[].ledger_status` | `ops_pipeline_run.orchestration_status` | ✅ |
| `runs[].deadline` | `ops_pipeline_run.hard_deadline_at` | ✅ |
| `runs[].planned·no_run_row` | `ops_reconciliation_issue` `issue_type=PLANNER_MISSING`·`scope=slot` | ✅ |
| `runs[].aws_status·aws_stop` | SFN `DescribeExecution` (`sfn_execution_arn` 이 원장에 있다) | ⚠️ AWS |
| `runs[].kind` | — | ❌ 계측 없음 |
| `tasks[]` | `ops_expected_task` + `ops_task_attempt`. `pipeline_type` 은 `ops_pipeline_run` 조인 | ✅ |
| `tasks[].completeness_*` | `ops_expected_task.completeness` jsonb | ✅ (분모 배선 3작업뿐) |
| `tasks[].max_retries` | — | ❌ `DatasetContract.retry_owner` 는 있지만 **상한 수가 없다** |
| `tasks[].last_ok`·`ok_rate` | — | ❌ 최근 N런 이력 집계라 단일 컬럼이 아니다 |
| `datasets[]` | `ops_expected_task` 의 `dataset_contract_*`·`freshness_*` 를 dataset 으로 묶어 **파생** | ⚠️ 아래 참조 |
| `chain.{feeds,stages}` | — | ❌ 소스 없음 (뉴스 갈래만 `/sources/lineage/news` 가 부분) |
| `queues[]` | SQS `GetQueueAttributes` | ⚠️ AWS |
| `queues[].purpose`·`subscribers` | — | ❌ SQS 가 안 준다 / 큐→서비스 구독 매핑 선언이 없다 |
| `outputs[].today` | 각 산출 테이블 count | ⚠️ 쿼리 신설 |
| `outputs[].base` | — | ❌ 일별 계열을 주는 응답이 없다 |
| `boundary` | `explanation_result ⋈ tenant_delivery` | ✅ (`seed_note` 는 소스 0) |
| `etf_ledger` | — | ❌ per-ETF 분석 귀결 원장이 없다 |
| `runbook` | `DatasetContract.runbook_uri` | ❌ 레지스트리 계약 1건, 그 값이 `None` |
| `meta.db`·`meta.today` | `now()` (DB 시계) · 거래일 | ✅ |

### `dataset_contract` 테이블은 없다

계약·신선도는 **`ops_expected_task` 의 컬럼**이다 — `dataset_contract_key`·`_version`·`_snapshot`·
`actual_as_of_date`·`collected_at`·`observed_at`·`freshness_status`·`_reason`·`_evidence`.
그래서 `datasets[]` 은 작업을 dataset 으로 묶어 파생한다.

계약 연결 작업은 **하나뿐**이고(`ETF_HOLDINGS_COLLECTION_KRX`, [ADR-0043](../adr/0043-dataset-contract-freshness.md)
첫 슬라이스), 그마저 `actual_as_of_date` 가 **설계상 영구 NULL** 이다 — KRX 응답에 요청한 `trdDd` 와
독립적인 as-of 증거가 없어 wrapper 가 관측에 성공해도 `UNKNOWN`·`ACTUAL_AS_OF_UNVERIFIED` 를 쓴다.

## 이 계약이 강제하는 것

**19룰 중 `facts` 단독 8개, `facts` + `/sources/minute` 11개가 실 응답만으로 선다.**

| | 룰 |
|---|---|
| ✅ 원장으로 돈다 | R01 · R02 · R04 · R05 · R06 · R07 · R09 · R14 (+ minute 이 오면 R17 · R18 · R19) |
| ⚠️ AWS 호출이 붙어야 | R03(SFN) · R12(SQS) |
| ❌ 계측이 없어 `못 돎` | R08 · R11 · R15 · R16 |
| ❌ 소스 자체가 없다 | R10 · R13 |

- 🔴 **R08 은 지금 `평가됨` 인데 실 응답에서 `못 돎` 이 된다.** 목이 가리고 있던 계측 공백이
  드러나는 것이다 — 회귀가 아니라 정정이다.
- **런북은 29/29 미등록**이 된다. 스냅샷의 8개 항목은 손으로 쓴 목이다.
- 못 도는 규칙은 화면에서 `못 돎` 으로 서야 한다. `평가됨 · 위반 0`("봤고 괜찮다")과 같은 칸에
  그리면 계측 공백이 정상으로 보인다.

## 배선 시 함께 해야 하는 것

정적 스냅샷이 채워 주거나 아예 안 담아서 **지금 드러나지 않는** 문제들이 이 계약을 붙이는 순간
같이 나온다. "배선만 바꾸면 된다"로 잡으면 전부 밟는다.

- `canRun` 이 없는 규칙 3건(R03·R10·R13) — 소스가 없는데 `평가됨 · 위반 0` 이 된다
- `meta.awsUnobservedRuns` — R03 의 부분 관측
- 사건 식별자(`vid`)가 **위치 인덱스**(`${rule}#${순번}`)다 — 앞 위반이 해소되면 뒤가 당겨져
  공유한 딥링크가 404 도 없이 다른 사건을 연다. 대체 키는 `${rule}:${targetId}` 인데 그것만으로는
  부족하다 — **R05·R06·R16 은 `target` 이 `task_key` 라 같은 작업이 여러 런에 걸리면 충돌한다**
  (셋 다 `runId` 를 들고 있으니 붙인다). 실시간 R17~R19 는 `runId` 가 없고 세션 identity 가
  `(dataset, source_group, session_date)` 인데 **어댑터가 `source_group` 을 버린다** — 실어야 한다.
  **충돌 시 fail loud.**
  ⚠️ 스냅샷은 이 충돌을 못 보여준다 — 같은 `task_key` 가 둘 이상 런에 걸린 경우가 없고 minute
  축은 아예 없다. 격자 30일치 실 응답에서는 날마다 반복된다.
- R02 가 `kind` 부재를 "정규 런"으로 단정한다 — 모름이 가장 강한 주장으로 기본값이 잡힌다
- `chain`·`outputs` 축 옵셔널화 (위 부재 규약). **`datasets` 는 넣지 마라** — 작업에서 파생하는
  축이라 못 보내는 축이 아니고, 옵셔널로 만들면 `canRun` 이 없는 R09 가 `평가됨 · 위반 0` 이 된다
- 평가기의 `note`(`R.note?.(f)`)가 `if (evaluated)` **밖**이라 `canRun` 이 못 막는 진입점이다.
  오늘 안 터지는 건 `note` 보유 룰이 R07 하나뿐이고 그게 필수 축(`tasks`)을 읽어서다 —
  옵셔널 축을 읽는 `note` 가 하나라도 붙으면 죽는다. `evaluated &&` 안으로 넣거나 널 가드를 규약화한다
- 화면 12곳이 사실을 **동기적으로** 읽는다(하나는 import 시점) — 비동기 전환이 필요하다
- **실행 상세로 가는 링크를 되살려야 한다.** 배선 전까지는 그 화면이 스냅샷 런만 해소해서
  실 API 화면(실행 이력 `/grid` · 현재 실행 `/minute` · 구성종목 결손)의 런을 못 연다.
  그래서 진입점 3곳을 끊고 사유 문구(`RUN_DETAIL_UNAVAILABLE`)를 세워 뒀다. 배선하면서
  같이 하지 않으면 실 응답이 붙은 뒤에도 세 화면은 "못 읽습니다"를 단 채 남는다.
  되돌릴 자리: `pages/GridPage.tsx` · `pages/MinutePage.tsx` · `pages/HoldingsImpactPage.tsx`
  의 조사 경로, `pages/ops/RunAxisPage.tsx` 의 미해소 안내, `pages/ops/investigation.ts` 의
  `RUN_DETAIL_UNAVAILABLE`, 그리고 그 부재를 강제하는 `pages/ops/investigation.test.ts` 단언.

## 남은 계측 부채

`kind` · `runbook_uri` 실값 · 재시도 정책 상한 수 · 큐→서비스 구독 매핑 · 산출 일별 계열 ·
체인 단계 집계 · per-ETF 분석 귀결 원장 · 작업 최근 이력 집계(`last_ok`·`ok_rate`).
