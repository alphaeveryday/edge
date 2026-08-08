# ADR-0048: 설명 S3 폴백 폐기 — 영속 전제는 LLM 앞에서 검사한다

- 상태: 승인됨
- 날짜: 2026-08-06

## 맥락

`analysis-engine` 은 설명을 영속할 때 FK 전제 세 가지를 요구한다 — `etf_profile` 존재 · `explanation_route` id ·
PUBLISHED `release_bundle`. 그 전제가 없으면 임의 FK 값을 만들지 않고 **설명을 S3 에 쓰고 성공을 반환**했다.
그 갈래는 `explanation_run` 행을 만들지 않는다.

문제는 분봉 소비자의 멱등 권위가 `explanation_run` 존재 하나뿐이라는 점이다(`has_run_for_route`, ALPHA-719).
폴백으로 끝난 런은 SQS 메시지를 지우지만 완료 표식을 안 남기므로, 가시성(900초)을 넘겨 재배달된 메시지가
다시 오면 프리플라이트가 **여전히 false** 라 같은 트리거에 LLM 파이프라인이 통째로 재실행된다.
route advisory lock(ALPHA-779)은 동시 처리 창만 닫는다 — 재배달 창은 run 행이 닫는데 이 갈래엔 그 행이 없다.

### 이 폴백은 죽은 코드가 아니었다

착수 시점 판단은 "CloudWatch 30일 실측 0건이니 이론적 결함"이었다(`persisted:"rds"` 634 / `"s3"` 0).
그 해석이 틀렸다. `explanation_prerequisites` 는 `bundle` 을 `ALPHAMALE_RELEASE_BUNDLE_VERSION` 에서 그대로
읽으므로 그 env 를 안 주면 **항상** 폴백이고, terraform 모듈 기본값은 `null`(=폴백 켜짐)이었다.
`envs/dev/main.tf` 주석이 그것을 명시하고 있었다 — _"미주입=의도적 S3 폴백"_.

즉 0건은 "갈래가 죽었다"가 아니라 "dev 가 bundle 을 주고 있다"의 결과였고, 폴백은 **env 하나로 켜는
설계된 운영 모드**였다. 이 계약이 terraform 주석에만 있고 ADR 이 없어 처음 조사에서 드러나지 않았다.

### 그 모드의 산출물은 아무도 안 본다

폴백이 쓰는 S3 객체(`{result prefix}/etf=…/trade_date=…/{request_id}.json`)를 읽는 소비자는 저장소 전체에
**0건**이다 — 콘솔·super-admin·publication·온프렘 어디에도 없다. 즉 켜면 "설명은 만들어지지만 원장·화면
어디에도 안 나타나는" 상태가 된다. `release_bundle` 버저닝이 잠정이던 시절(ALPHA-406)의 임시 우회다.

## 결정

**S3 폴백을 폐기하고, 영속 전제 검사를 LLM 호출 앞으로 옮긴다.**

- `pipeline._persist_explanation` 의 폴백 분기와 `archive.write_explanation_to_s3` · `_FALLBACK_EVENT_FIELDS` ·
  `explanation_result.skipped` 로그 이벤트를 삭제한다.
- 전제 검사를 `persist_observation_route` 직후(LLM 앞)에 두고 결손이면 `PipelineError` 로 런을 세운다.
  전제 셋은 모두 그 시점에 확정된다 — `profile`·`bundle` 은 이 런이 만드는 게 아닌 사전 상태이고,
  `route` 는 바로 위가 커밋한다. "이미 태웠으니 결과를 버리기 아깝다"는 폴백의 존재 이유가 성립하지 않는다.
- `analysis_release_bundle_version` 을 `nullable = false` + 빈 문자열 `validation` 필수 변수로 만든다.
  기본값 제거는 누락을, validation 은 빈 값을 막는다 — 서로 대신하지 못한다. 런타임 exit 1 보다
  plan 단계에서 막는 편이 싸다.

## 대안

**폴백을 유지하고 재배달 중복만 막는다.** 폴백 성공 시에도 route 기준 완료 표식을 남기는 방법.
`explanation_run` 은 `release_bundle` FK 를 요구하므로 그 표를 못 쓰고 별도 마커 표 + 마이그레이션이 필요하다.
아무도 읽지 않는 산출물을 살리기 위해 원장에 표를 하나 더 만드는 교환이라 택하지 않았다.

**폴백 모드를 명시적 스위치로 승격한다.** `EXPLANATION_FALLBACK_MODE` 류 플래그로 의도를 드러내는 방법.
운영 모드로서의 값이 소비자 0건이라 확인되지 않아, 스위치를 정교하게 만드는 대신 축을 없애는 편이 싸다.

## 결과

- 얻는 것: 재배달 LLM 중복 과금 경로가 소멸한다(표식 없는 완료가 안 생긴다). 전제 결손일 때 LLM 을 태우고
  나서 버리는 낭비가 없어진다. 결손이 S3 에 조용히 쌓이는 대신 DLQ·비0 종료로 드러난다(Rule 12).
- **EOD 레인의 폭발 반경이 바뀐다.** 세 전제 중 결손이 실제로 갈리는 건 `profile` 뿐이고(종목별), 그것은
  사람이 채우는 마스터라 자기치유가 없다. 신규 ETF 를 유니버스에 넣고 `etf_profile` 을 안 채우면 그 한
  종목의 비0 종료가 전량성공 게이트([ADR-0028](0028-unified-pipeline-sfn.md))를 통해 **유니버스 일일 런
  전체**를 실패시키고, 채울 때까지 매일 빨갛다. 이 대가를 의식적으로 받는다 — 설명이 없는 ETF 가 조용히
  초록에 묻히면 아무도 안 채운다. 같은 종류의 선례가 같은 파일에 이미 있다(`resolve_etf_instrument` 결손,
  ALPHA-467). `holdings` 빈은 선례가 아니다 — 그건 매일 적재되는 데이터라 다음날 자연 복구된다.
- 잃는 것: bundle 버저닝이 미비한 신규 환경을 "설명은 돌되 원장에는 안 넣는" 모드로 띄우는 경로. 그런 환경이
  생기면 `release_bundle` 시딩이 선행 조건이 된다.
- 런 아카이브 계약: 전제 결손 런은 아카이브도 안 남는다(raise 가 `write_run_archive` 앞이다). 기존 raise
  경로(`resolve_etf_instrument`·holdings 빈·수익률 미착지)도 이미 그러하므로, "매 런 1건" 이라던 README
  문구를 "완주한 런마다" 로 정정했다.
- 구현: [ALPHA-797](https://alphaeveryday.atlassian.net/browse/ALPHA-797).
