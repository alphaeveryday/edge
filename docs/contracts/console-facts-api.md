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
| `runs[].kind` | — | ❌ 계측 없음 (그래서 `RunFact.kind` 는 **옵셔널**이다 — 부재는 '정규'가 아니다) |
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
| ⚠️ AWS 호출이 붙어야 | R03(SFN) · R12(SQS) — 둘 다 `canRun` 이 있어 미배선이면 `못 돎` 이다 |
| ❌ 계측이 없어 `못 돎`(`notRun: 'axis'`) | R08 · R10 · R11 · R13 · R15 · R16 (+ SFN 미배선이면 R03, `queues` 미배선이면 R12, minute 미도착이면 R17~R19) |

⚠️ **`못 돎` 의 생산자는 둘이다.** 위 표는 `canRun`(사실 축 부재, `notRun: 'axis'`)만 열거한다.
응답이 사건을 못 가르게 주면(식별자 충돌·빈 대상/범위 축) 평가기가 그 규칙의 위반을 통째로
버리고 `notRun: 'identity'` 로 세운다 — **계측 공백이 아니라 계약 위반**이라 계측 부채
대시보드에 매핑하면 안 된다. `rules[].notRun` 필드가 그 구분을 낸다(리포트 JSON 에도 있다).

- 🔴 **R08 은 지금 `평가됨` 인데 실 응답에서 `못 돎` 이 된다.** 목이 가리고 있던 계측 공백이
  드러나는 것이다 — 회귀가 아니라 정정이다. **R03·R10·R13 도 같이 뒤집힌다**(ALPHA-738 A2 에서
  `canRun` 을 붙였다): 동봉 스냅샷은 세 축을 다 채우고 있어 오늘은 `평가됨` 이지만, 실 응답에서
  SFN 조회·체인 집계·산출 일별 계열이 없으면 셋 다 `못 돎` 으로 선다. 스냅샷 회귀 수(위반 29 ·
  사건 20)는 안 바뀐다 — 이 셋의 판정이 오늘 화면에서 달라지지 않는다는 뜻이다.
- **런북은 29/29 미등록**이 된다. 스냅샷의 8개 항목은 손으로 쓴 목이다.
- 못 도는 규칙은 화면에서 `못 돎` 으로 서야 한다. `평가됨 · 위반 0`("봤고 괜찮다")과 같은 칸에
  그리면 계측 공백이 정상으로 보인다.

## 배선 시 함께 해야 하는 것

정적 스냅샷이 채워 주거나 아예 안 담아서 **지금 드러나지 않는** 문제들이 이 계약을 붙이는 순간
같이 나온다. "배선만 바꾸면 된다"로 잡으면 전부 밟는다.

- ✅ **`canRun` 이 없던 규칙 3건(R03·R10·R13) — 해소됨**(ALPHA-738 A2, `rules/rules.ts`).
  축은 각각 `runs[].aws_status` 하나라도 관측됨 · 한 레인에 비교 가능한 점 2개 이상(`chainPoints`,
  blind 는 값이 아니라 빠짐이라 점이 아니다) · `outputs[].base` 하나라도 있음이다.
  **점 임계가 1이 아니라 2인 이유**: 점이 하나면 "인접 감소"라는 물음 자체가 성립하지 않는다.
  셋 다 `dep` 에 없던 계측 이름을 넣었다 — 아래 사유 문장 항목이 그걸 쓴다.
  ⚠️ **`canRun` 의 축은 `run()` 의 필터와 같은 검사여야 한다.** R03 이 `!= null`, `run()` 이
  truthy 였던 동안 `aws_status: ''` 응답은 `canRun` 을 통과하고 `run()` 에서 걸러져 다시
  "평가됨 · 위반 0"이 됐다(리뷰가 잡았다). R13 의 `!!o.base` 도 같은 이유다(`base: 0` 은
  나눗셈이 성립하지 않아 `run()` 이 거른다).
  ⚠️ **R10 의 `||` 는 의도된 선택이다.** 규칙 단위 `canRun` 으로는 갈래를 못 가르는데, `&&` 로
  조이면 한 갈래만 도착한 응답에서 **볼 수 있는 P0 손실을 통째로 버린다**. 대신 `note` 가 어느
  갈래를 실제로 비교했는지 밝힌다 — "위반 0건"이 두 갈래 다 괜찮다는 뜻이 아니라는 것을
  그 자리에서 말한다.
- `meta.awsUnobservedRuns` — R03 의 **부분** 관측. ⚠️ R03 의 `canRun` 은 이 축을 답하지 **않는다**:
  40런 중 1건만 관측돼도 참이라 나머지 39런은 비교조차 안 하고 "평가됨 · 위반 0"에 들어간다.
  `every` 로 조이면 SFN 실행이 애초에 없는 런 하나가 전체를 `못 돎` 으로 만들어 **볼 수 있는
  불일치를 통째로 버린다** — 규칙 단위 불리언으로는 "일부만 봤다"를 표현할 수 없어서 이 축이
  따로 필요한 것이다. 배선 전까지 그 칸은 부분 관측을 전건 관측처럼 보여준다(알려진 부채).
- ✅ **사건 식별자(`vid`) — 해소됨**(ALPHA-738 A1, `rules/evaluate.ts`. 조각 가드는 A2 에서 닫혔다 — 아래).
  위치 인덱스(`${rule}#${순번}`)였고 앞 위반이 해소되면 뒤가 당겨져 공유한 딥링크가 다른 사건을 열었다. 지금은
  `${rule}:${targetId}` + 범위가 있으면 `@${scope ?? runId}` 다.
  **두 축을 가른다**: `targetId` 는 *무엇이 고장났나*(런북 키 `${rule}.${targetId}` 가 쓰는 축이라
  **날짜가 들어가면 안 된다** — 키가 매일 달라져 어떤 조치도 등록 못 한다), `scope` 는 *어느 실행
  인스턴스인가*. 배치는 런 키가 범위를 겸하고, 실시간 R17~R19 는 `targetId=dataset/sourceGroup` ·
  `scope=세션 날짜`(어댑터가 버리던 `source_group` 을 싣는다).
  충돌 시 **그 규칙만 `못 돎`** 으로 세운다 — 던지면 나머지 18규칙의 사건까지 화면에서 사라진다.
  범위가 빈 문자열이면 충돌 없이도 `못 돎` 이다(`''` 를 '없음'으로 읽으면 시간 축 충돌이 난다).
  ⚠️ **딥링크는 경로가 아니라 쿼리다**(`/ops/incidents/detail?vid=…`). CloudFront SPA fallback
  (`infra/terraform/modules/static-site/spa-rewrite.js`)이 **마지막 경로 조각의 점(.)** 으로 정적
  파일을 가르는데, 대상 id 에 점이 든 사건이 있다(`R13:o.pub`·`R15:analyze.failed`). 경로에 두면
  공유 링크·새로고침만 죽는다. **새 라우트에 점이 들 수 있는 파라미터를 경로로 두지 마라.**

  ✅ **합성 축의 조각 가드 — 해소됨**(A2). 자리는 **`rules/rules.ts` 의 `compose(sep, ...parts)`**
  하나다: 조각이 하나라도 비면 합성을 안 하고 빈 문자열을 내, 엔진의 기존 빈 축 가드가 그 규칙을
  `못 돎(identity)` 으로 세운다. 새 가드를 만들지 않고 **이미 있는 판정 경로 하나**를 쓴다.
  ⚠️ **합성하는 자리는 둘이다** — 세션 축 `${dataset}/${sourceGroup}`(R17~R19)과 체인 축
  `${src}:${s.id}`(R10). 처음 고칠 때 세션 축만 막고 체인 축을 놓쳤고 리뷰가 잡았다: "가드가
  둘이면 한쪽만 고쳐진다"는 이 항목 자신의 논거가 그 상태를 그대로 기술하고 있었다. **새로
  합성하는 대상 축이 생기면 인라인으로 쓰지 말고 이 함수를 통과시켜라.** 구분자가 자리마다 다른
  것은 런북 키가 그 모양에 매여 있어서다(`R10.batch:c.run`) — 통일하면 등록된 조치를 못 찾는다.
  어댑터(`consoleFacts.minuteFacts`)는 빈 벤더를 **그대로 옮긴다**: 거기서 메우면
  (`?? '알 수 없음'`) 하류 가드가 영원히 안 뜨는 죽은 분기가 되므로, 어댑터 테스트는
  '메우지 않는다'만 단언한다.

  ✅ **같은 뿌리였던 소비자 쪽 — `investigation.ts` 도 같이 고쳤다**(A2). 벤더 부재를 "이 수는
  날짜 축 집계다"의 판별자로 쓰고 있었다. 조각 가드가 `price_minute/` 를 막아 문자열 판별은
  이제 성립하지만, **판별이 성립하는 것과 문장이 참인 것은 다르다**: 세는 값이 아예 없는
  R09(신선도 판정 불가)도 같은 `ds-price_minute` 앵커로 그 분기에 오고, 그때 저 문장은 거짓이다.
  그래서 문장을 **아는 것까지만**으로 내렸다 — "이 사건은 벤더를 지목하지 않아…". 조치 안내
  ("원장 화면에서 벤더를 골라라")는 그대로 남는다.
  왕복 단언은 `investigation.test.ts` 에 있다: 손으로 만든 위반이 아니라 **엔진이 낸 위반**을
  넣고, 나온 조사 문맥이 `facts.minute.sessions` 의 **실물 행과 맞는지**로 검사한다 — 문자열
  모양을 다시 적으면 합성 순서를 뒤집어도(`kis/price_minute`) 전건 통과한다.
- ✅ **`canRun` 이 거짓인 *사유 문장* — 해소됨**(A2). **`Rule` 에 새 필드를 넣지 않았다.**
  사유는 이미 있던 `dep`("어떤 계측이 없는가")가 나르고, `notRunReason` 이 그걸 쓴다 —
  `canRun` 이 붙은 규칙에 `dep` 이 비어 있던 것이 문제였지 필드가 없던 게 아니었다.
  드리프트는 **집합 불변식**이 막는다(`pages/ops/notRun.test.ts`): `canRun` 을 가지고 `axis` 가
  없는 규칙들의 사유 문장은 **서로 달라야 한다**. 사유 없이 붙는 새 규칙은 폴백 문장을 받아
  R12 와 충돌해 그 자리에서 깨진다 — 규칙마다 손으로 문장을 다는 표를 만들면 반드시 낡는다.
  `axis: 'minute'` 규칙(R17~R19)은 이 검사에서 제외한다: 같은 축·같은 조회 상태면 같은 문장이
  맞고, 갈려야 하는 것은 **축이 서로 다른 쪽**이다.
- ✅ **`deadJobsByDate` — `MinuteFacts.deadJobsByDate: string[]` 로 올렸다**(A2). 같은 데이터셋의
  세션이 서로 다른 축을 갖는 상태가 이제 **표현 불가**다. 배열이지 `Set` 이 아닌 이유는 이 사실이
  결국 JSON 응답에서 오기 때문이다. `[]` 는 부재 규약의 거짓 0 이 아니라 "날짜 축인 데이터셋이
  없다"는 실측이다 — 생산자가 모든 데이터셋을 보고 판정한다.
  🔴 **그래서 이 필드는 옵셔널이 아니다(와이어에서도 필수).** 생략을 허용하면 소비자가 `?? []` 로
  접고 "생산자가 안 말했다"와 "날짜 축 데이터셋이 없다"가 구분 불가가 된다. 그리고 축을 세션에서
  데이터셋으로 올리며 **폭발 반경이 커졌다**: 예전엔 세션 하나가 축을 빠뜨렸지만 이제 필드 하나가
  빠지면 모든 데이터셋이 한꺼번에 세션 축으로 되돌아가 뉴스 DEAD 가 벤더 수만큼 복제된다.
  필수로 두면 그 확률이 0 이다(리뷰 지적).
- ✅ **`deadJobs: null`(모름)이 판정 층에서 소멸하던 것 — 해소됨**(A2). 어댑터는 어휘 밖
  데이터셋의 job 원장을 `null` 로 내는데(0으로 접으면 원장 부재가 "봤고 괜찮다"가 되므로),
  R19 는 그걸 **건너뛰기만** 해서 `null` 과 `0` 이 똑같이 `평가됨 · 위반 0` 이었다 — 어댑터가
  지킨 구분이 규칙 층에서 죽던 자리다(리뷰가 잡았다). 지금은 원장을 하나도 못 읽으면 `못 돎`,
  일부만 못 읽으면 `note` 가 그 데이터셋을 이름으로 밝힌다.
  ⚠️ **세션이 0건인 것은 다르다** — 그건 실측이고 잃을 후속 작업 자체가 없다. 못 돎으로 세면
  실시간 레인이 안 도는 날마다 거짓 `못 돎` 이 뜬다.
- ✅ **R02 의 `kind` 부재 단정 — 해소됨** / ⚠️ **R04 는 기본값의 방향만 뒤집혔다**(A2).
  `RunFact.kind` 를 옵셔널로 내리고 표기를 넷으로 갈랐다(정규·수동·백필·**미기록**).
  R04 는 `kind === 'scheduled'` 를 **요구**하던 것을 "수동·백필로 **확인된** 런만 배제"로 바꿨다 —
  모름은 배제 근거가 아니다. 요구로 두면 원장 공백 + AWS 실패를 통째로 놓친다(P0 거짓 음성).
  🔶 **다만 "사문화가 해소됐다"는 과장이다**(리뷰 지적): `kind` 가 없으면 배제 분기도 영원히 안
  돈다 — 바뀐 것은 사문화 여부가 아니라 **기본값이 제외에서 포함으로** 간 것뿐이다. 명세 §2 R04
  의 경계(수동·백필의 원장 공백은 실패 단정 근거가 아니다)는 **`kind` 계측이 붙어야 구현된다** —
  아래 「남은 계측 부채」에 그대로 남아 있다. 그때까지는 `why` 가 "런 종류 미기록"을 같이 말한다.
  ❌ 열린 질문: 종류를 모른 채 `state: 'FAILED'` 로 **확정**을 그리는 것이 이 콘솔의 부재 규약과
  맞는가(리뷰 제기). `state` 는 원장·AWS 가 실제로 낸 값이고 불확실한 것은 *종류*뿐이라 지금은
  `why` 로 충분하다고 봤다 — `kind` 계측이 붙으면 자연히 사라지는 물음이다.
- ✅ **R04 의 종료 실패 어휘가 원장·AWS 두 벌이던 것 — 해소됨**(A2). AWS 쪽 목록에만 `ABORTED` 가
  빠져 있어 원장 공백 + SFN `ABORTED`(운영자가 멈췄는데 투영이 안 된 상태)가 통째로 안 잡혔다.
  한 상수(`TERMINAL_FAILURE`)로 합쳤다 — 두 벌이면 한쪽만 늘어난다.
  ⚠️ **남은 것 — 화면 3곳은 단언이 없다.** `pages/ops/RunAxisPage.tsx` 의 목록 칩·상세 한 줄이
  `kind` 부재를 `<Absent kind="uninstrumented" />` 로 그리고, **같은 표의 캡션**이 그 어휘를
  말로 설명한다. 이 앱에는 컴포넌트 테스트가 없고(`node --test` 로 `.ts` 만 돈다) 스텁에 `runs`
  자체가 없어 **E2E 로도 안 밟힌다** — `KIND_LABEL[r.kind ?? 'scheduled']` 같은 변이도, 캡션이
  다시 "이 열은 목값" 으로 되돌아가는 것도 아무것도 안 잡는다(캡션은 실제로 셀과 다른 부재
  어휘를 말하고 있었고 리뷰가 잡았다). `runs[].kind` 를 실제로 싣게 되는 날 세 자리를 함께 봐라.
- `chain`·`outputs` 축 옵셔널화 (위 부재 규약). **`datasets` 는 넣지 마라** — 작업에서 파생하는
  축이라 못 보내는 축이 아니고, 옵셔널로 만들면 `canRun` 이 없는 R09 가 `평가됨 · 위반 0` 이 된다.
  🔴 **옵셔널화하는 순간 `canRun` 이 새 진입점이 된다**(리뷰 지적). `canRun` 은 `evaluated` 와
  무관하게 **모든 규칙에 대해 무조건** 불리므로, R10 의 `chainPoints` 가 `f.chain.feeds` 를 읽다
  죽으면 평가가 통째로 사라진다 — 19규칙의 사건이 전부. `note` 진입점을 막은 것과 같은 종류이고,
  같은 처방이 필요하다(널 가드 + 축이 빈 사실에 대고 전 규칙의 `canRun` 을 부르는 집합 순회).
- ✅ **평가기의 `note` 진입점 — 해소됨**(A2). ⚠️ **이 항목이 예전에 적고 있던 조치는 함정이었다**:
  "`evaluated &&` 안으로 넣어라"를 문면대로 하면 `note: collision ?? R.note?.(f) ?? R.dep` 에서
  **충돌 사유가 지워진다** — 충돌이 나면 위에서 `evaluated` 를 이미 false 로 바꾸기 때문이다.
  실제로 넣은 것은 `collision ?? (evaluated ? R.note?.(f) : null) ?? R.dep` 다: `collision` 은
  반드시 밖에 있어야 한다. 그리고 이 널 가드 자체는 **오늘 어떤 단언도 안 잡는다**(`canRun` 과
  `note` 를 둘 다 가진 규칙이 없었다 — 이번에 R10·R19 가 생겨 이제는 잡힌다). 막으려던 죽음은
  한 층 위에서도 잡는다 — `rules.test.ts` 가 **모든** 규칙의 `note` 를 축이 빈 사실에 대고
  부른다(집합 순회).

  **`rules[].note` 의 세 갈래는 배타적이다**(리뷰 지적으로 확정):
  `collision`(응답 결함 사유) / **돈** 규칙의 `R.note` / **못 돈** 규칙의 `R.dep`.
  `?? R.dep` 폴백으로 두면 배선돼서 잘 도는 규칙 행에도 "…배선" 주석과 `*` 표가 영구히 붙는다 —
  R03·R10·R13 에 `dep` 을 넣자마자 실제로 그랬다. `dep` 은 **못 돈 사유**이지 돌아간 규칙의
  주석이 아니다. 소비자(`notRunReason`)는 `note` 를 사유로 읽지 않고 `RULES` 에서 직접 읽는다.
- 화면 12곳이 사실을 **동기적으로** 읽는다 — 비동기 전환이 필요하다. 그중 하나는 **import 시점**
  이다: `pages/ops/trendCatalog.ts` 의 `METRICS`(모듈 최상위 `export const`)가 평가되며
  `output('o.doc')!.today`(`:273`·`:275`)·`output('o.trig')!.today`(`:452`·`:453`)를 읽는다.
  축이 비면 렌더가 아니라 **모듈 평가**에서 죽어 `AdminLayout` 의 ErrorBoundary 밖이다(흰 화면).
  `buildMetrics(facts)` 로 뒤집으면서 **그 `!` 4개를 같이 없애야** 한다. 같은 파일의
  `chain('c.res') ?? 0`(`:367`·`:395`)은 부재를 0 으로 그려 "결과 생성률 0%" 라는 거짓 경보를
  낸다 — `coverageMetric`·`lagMetric` 이 쓰는 `comparisonType: 'uninstrumented'` 규약을 따라야 한다.
- **실행 상세로 가는 링크를 되살려야 한다.** 배선 전까지는 그 화면이 스냅샷 런만 해소해서
  실 API 화면(실행 이력 `/grid` · 현재 실행 `/minute` · 구성종목 결손)의 런을 못 연다.
  그래서 진입점 3곳을 끊고 사유 문구(`RUN_DETAIL_UNAVAILABLE`)를 세워 뒀다. 배선하면서
  같이 하지 않으면 실 응답이 붙은 뒤에도 세 화면은 "못 읽습니다"를 단 채 남는다.
  적용 기준은 "상세냐 목록이냐"가 아니라 **그 자리에서 사용자가 특정 런 identity 를 쥐고
  있는가**다. 쥔 자리에서 스냅샷 런 축으로 내보내면 "이 실행은 없다"로 읽힌다. 안 쥔 자리
  (`SourcesPage` 의 조사 문맥 없음 분기 등)는 목록이 정확히 그 용도라 대상이 아니다.

  되돌릴 자리 — **상수를 참조하는 곳은 컴파일러가 잡지만 순수 문구는 아무것도 안 잡는다.**
  | | |
  |---|---|
  | 컴파일러가 잡음 | `pages/ops/investigation.ts` 의 `RUN_DETAIL_UNAVAILABLE` 과 그 사용처(`GridPage`·`MinutePage`), 부재를 강제하는 `pages/ops/investigation.test.ts` 단언 |
  | 순수 문구 — 손으로 찾아야 함 | `GridPage.tsx` 격자 힌트("배치 실행은 여기까지입니다")·표 아래 문단 · `RunAxisPage.tsx` **부재 안내 2곳**(빈 목록 · 미해소 상세) · `HoldingsImpactPage.tsx` 조사 경로와 404 분기 |
  | 접근성 | `styles/minute.css` — 링크가 아니게 되면서 `.mn-runcard` 의 `:hover`·`:focus-visible` 을 지웠다. 카드를 다시 `<Link>` 로 만들면서 안 되살리면 **포커스 링 없는 링크**가 된다(이 앱의 `styles/` 에 전역 `a:focus-visible` 이 없다) |

## 남은 계측 부채

`kind` · `runbook_uri` 실값 · 재시도 정책 상한 수 · 큐→서비스 구독 매핑 · 산출 일별 계열 ·
체인 단계 집계 · per-ETF 분석 귀결 원장 · 작업 최근 이력 집계(`last_ok`·`ok_rate`).
