# ETF 층별 사건 설명과 주장 단위 근거 설계

## 목적

요구 시간창의 ETF 움직임을 시장·섹터·고유로 동시에 분해하고, 각 층에서 탐색 격자가 고른 후보 중 ATT 관문을 통과한 실제 사건을 빠짐없이 쉬운 설명에 싣는다. 각 주장 블록은 `{statistical, bundle_id}`로 영속 근거를 가리킨다.

## 현재 결함

- `route_etf()`의 단일 `kind`가 대표 제목과 실행 스위치를 겸해 혼합 경로에서 고유종목을 잃는다.
- 혼합 경로의 `targets`가 비어 시장·섹터만 실행되고 고유종목 ATT가 실행되지 않는다.
- 복수 섹터 중 첫 층만 검정한다.
- `interval.observe()`의 `run_trial()`은 `layer`를 넘기지 않아 모든 사건을 기본 고유층으로 검정한다.
- 쉬운 설명의 창 순위는 기준이 드러나지 않고, 대괄호 수치는 감사 데이터를 사용자 문장에 노출한다.
- 통계 번들은 뉴스·스레드·시계열 계보를 함께 보존하지 못한다.

## 층 선택

한 요구창의 회계는 다음을 동시에 유지한다.

$$r_w=c_{market,w}+\sum_s c_{sector_s,w}+\epsilon_{idio,w}$$

`Route.kind`와 `DOMINANT`는 대표 제목에만 사용한다. ATT 실행층은 독립적으로 고른다.

- 시장: 채택된 시장층의 절대기여가 창의 전체 절대기여 중 20% 이상.
- 섹터: 채택된 각 섹터층의 절대기여가 20% 이상. 복수 섹터를 모두 허용한다.
- 고유: 고유 자격을 통과하고 고유 절대기여가 20% 이상이며 `MIN_NAME_SHARE`를 넘는 구성종목을 모두 대상으로 한다.

20%는 기존 `interval.FLOOR`를 재사용한다. 새 임계나 설정을 만들지 않는다. 한 창에서 시장·복수 섹터·복수 고유종목이 동시에 활성화될 수 있다.

## 층별 사건 검정

### 시장

시장층이 활성화되면 `mkttrial.screen_market()`의 거래일 단위 시장사건 격자를 사용한다. Bonferroni 임계와 사전추세를 통과한 사건을 전부 설명한다.

### 섹터

`grid_screen(layer="섹터")`로 해당 층 결과변수에서 사건타입×노출 후보를 고른 뒤 `run_trial(layer="섹터")`로 확인한다. 다음을 모두 만족한 후보만 설명한다.

- $p < \alpha/m$
- `balanced is True`
- `pretrend_ok is True`

각 활성 섹터를 독립적으로 실행한다.

### 고유

각 활성 고유종목에 대해 `grid_screen(layer="고유")` 후보를 `run_trial(layer="고유")`로 확인한다. 섹터와 같은 ATT·균형·사전추세 관문을 쓴다.

격자와 ATT가 같은 표본을 사용하는 현재 선택 편향은 번들의 `selection_caveat="same_sample_grid_selection"`에 기록한다. 화면에서는 원인 확정이 아니라 과거 비교에서 구별돼 설명에 포함됐다고 말한다.

## 쉬운 설명 계약

출력 순서는 요구창 방향, 시장, 각 섹터, 통과한 실제 사건 전부, 미설명 고유 잔여다.

- `두 번째로 크게` 등 창 순위를 쓰지 않는다.
- `[구간 몫 ...]` 등 대괄호 근거를 쓰지 않는다.
- 한 주장은 여러 문장일 수 있다.
- 근거가 있는 주장 블록 마지막에 정확히 `{statistical, ev_<16hex>}`를 붙인다.
- 화면 태그에는 `sign`을 싣지 않는다. `sign`은 번들 필드로 유지한다.
- 실제 사건은 회사·사건 종류를 직접 말한다.
- 별도의 “일단위라 이 시간 효과로 단정할 수 없다” 면책 문장은 쓰지 않는다. 대신 “같은 사건이 있었던 날에는 … 차이가 반복됐고, 오늘 이 구간에서도 같은 방향이었다”로 일별 비교와 창 관측을 문장 안에서 분리한다.
- ATT가 성립하지 않은 사건을 원인처럼 말하지 않는다. 활성층은 측정됐지만 통과 사건이 없다는 사실은 별도 주장으로 말할 수 있다.

예시:

```text
오후 1시 30분부터 3시 30분까지 ETF 가격이 올랐어요.

시장 전체가 함께 오른 부분이 있었어요. 관세 정책이 바뀐 날에는 국내 시장도 평소보다 더 오르는 차이가 반복됐고, 오늘도 같은 방향으로 움직였어요. {statistical, ev_1111111111111111}

시장 움직임을 빼고도 반도체 업종에서 함께 오른 부분이 있었어요. 반도체 수출 규제가 바뀐 날에는 이 업종이 평소보다 더 오르는 차이가 반복됐고, 오늘도 같은 방향으로 움직였어요. {statistical, ev_2222222222222222}

삼성전자가 실적을 발표한 날에는 ETF의 고유한 부분이 평소보다 더 오르는 차이가 반복됐고, 오늘 이 구간에서도 같은 방향으로 움직였어요. {statistical, ev_3333333333333333}
```

## 주장 단위 근거 번들

기존의 통계=`stats`만, 서사=`news_ids`만 허용하는 상호배타 규칙을 폐기한다. 실제 사건을 분석하는 `statistical` 번들은 다음을 함께 가진다.

- `bundle_id`, `basis`, `cell`, `trade_date`, `layer`, `claim`, `sign`
- `stats` JSONB
- `series_lineage` JSONB
- `news_ids` TEXT[]
- `thread_ids` TEXT[]

한 사건 주장마다 번들 하나를 만든다. 서로 다른 사건을 한 번들로 합치지 않는다. 시장·섹터 회계 주장처럼 뉴스가 필요 없는 통계 주장은 `news_ids`와 `thread_ids`가 빈 배열일 수 있다.

### stats

- 격자: `type`, `exposure`, `p2`, `n`, `rank`, `selected_count`
- ATT: `etype`, `slots`, `layer`, `att`, `p`, `alpha_adjusted`, `pairs`, `treated`, `dates`
- 보정: `att_adj`, `p_adj`, `smd`, `balanced`, `lead`, `pretrend_ok`, `null_kind`
- 조절자가 있으면 `selected`, `pi`, `p_step`, `p_max`
- `selection_caveat`

### series_lineage

원시 관측값을 복제하지 않는다. 사용한 시계열을 다시 조회할 수 있는 계약만 저장한다.

- `dataset`, `table` 또는 `view`
- `series` 또는 `field`
- `entity_ids`와 처치·대조 식별축
- `grain`, `start`, `end`, `as_of`
- `transform`, `filters`
- `matching`: 업종, 캘리퍼, `k`, 처치·대조 조건
- `source_keys` 또는 파티션
- 원천이 제공할 때 `checksum`, `snapshot`, `version`

### 뉴스 계보

- `news_ids`: 그 주장에 실제 사용한 문서 ID만.
- `thread_ids`: 그 주장에 실제 사용한 스레드 ID만.
- 같은 날 뉴스 전체나 선택되지 않은 재보도는 넣지 않는다.

`bundle_id` 내용 해시에는 `stats`, `series_lineage`, 정렬된 `news_ids`, 정렬된 `thread_ids`, `sign`을 모두 포함한다.

## 저장과 호환성

Flyway 마이그레이션으로 `series_lineage JSONB NOT NULL DEFAULT '{}'`와 `thread_ids TEXT[] NOT NULL DEFAULT '{}'`를 추가한다. 기존 행은 기본값으로 유효하다. `news_ids`는 statistical 번들에서도 허용하되, grounded 제약은 다음을 유지한다.

- narrative: `news_ids`가 하나 이상.
- statistical: `stats`가 비어 있지 않음.

기존 `sign` 컬럼은 유지한다. 산문 태그만 3필드에서 2필드로 바꾼다.

## 검증 기준

- 합성 혼합 Rollup에서 시장·복수 섹터·고유종목이 동시에 활성화된다.
- 각 활성층에서 통과한 사건이 하나도 누락되지 않고 사건별 태그를 가진다.
- 섹터 후보 선택과 ATT가 모두 `layer="섹터"`, 고유는 모두 `layer="고유"`를 사용한다.
- 무유의·불균형·사전추세 실패 사건은 원인 주장에 들어가지 않는다.
- 쉬운 설명에 창 순위와 대괄호 근거가 없다.
- 각 `{statistical, bundle_id}`가 DB 행 하나로 해소된다.
- 통계 사건 번들에 stats·series_lineage·사용한 news_ids·thread_ids가 보존된다.
- 기존 번들 행과 narrative 번들은 마이그레이션 후에도 유효하다.
