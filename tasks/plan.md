# ETF 요청창 블록 출력 구현 계획

## 기준

- 승인 명세: `docs/superpowers/specs/2026-08-05-etf-window-block-output-design.md`
- 성공 조건: 분봉 설명은 요청창 하나만 설명하고, 코드가 고른 고정 순서 블록만 출력한다. LLM은 허용 자리표시자만 작성하며 수치·시각·이름·출처는 코드가 치환·검증한다.
- 비범위: 리포트, 갱신/supersedes/철회/재발행/준법 정정, 창 단위 인과로 가장하는 일단위 검정.

## 구현 순서

### 1. 요청창 사실 모델과 결정론적 블록 계획

대상:
- `src/apps/cloud/analysis-engine/src/edge_analysis/statics/interval.py`
- `src/apps/cloud/analysis-engine/tests/statics/test_interval.py`

변경:
- 기존 창 계산을 재사용해 `WindowFacts`를 만든다. 헤더용 전일 종가 대비 ETF 누적수익과 설명용 `window_start~window_end` 수익을 별도 필드로 둔다.
- 요청창의 ETF/NAV/구성종목 기여·폭/시장/섹터/고유/경로/이례성/사건·뉴스·수급/검정/계보만 담는다. 이전·이후 창의 가격, 방향, 문장은 버린다.
- `BlockPlan`은 명세의 14개 순서를 코드 상수로 고정한다. 필수 블록은 결측 사유를 내고, 조건부 블록은 게이트를 통과할 때만 생성한다.
- 통계 블록의 전역 하한을 `MIN_N=20`으로 고정한다. `n<20`이면 평균·효과·p값을 모두 제거하고 표본 부족 문구만 남긴다.
- 기존 `explain()`은 새 사실/계획/렌더 함수를 호출하는 얇은 공개 래퍼로 남긴다. 하루의 다른 창을 설명하던 출력은 제거한다.

수용 기준:
- 헤더 시각은 `window_end`, 헤더 수익률은 전일 종가 기준이다.
- 나머지 수치와 문장은 요청창 기준이고 요청창 밖 시각·가격·방향이 없다.
- 시장·섹터 미계측은 고유 0으로 위장하지 않고 각각 사유를 출력한다.
- 같은 `WindowFacts`는 같은 블록 순서와 발생 여부를 만든다.

검증:
- 요청창 밖 큰 변동을 가진 fixture에서도 출력에 그 변동·시각이 없는 단위 테스트.
- 필수/조건부 블록 순서, 결측 사유, `MIN_N=19/20` 경계 테스트.

### 2. 구성종목·NAV·PIT 근거를 요청창으로 절단

대상:
- `src/apps/cloud/analysis-engine/src/edge_analysis/statics/interval.py`
- `src/apps/cloud/analysis-engine/src/edge_analysis/statics/premium5.py`
- `src/apps/cloud/analysis-engine/src/edge_analysis/statics/evidence.py`
- 관련 `tests/statics/test_interval.py`, `test_premium5.py`, `test_evidence.py`

변경:
- 기존 5분봉과 보유비중을 사용해 요청창 구성종목 수익·기여와 상승/하락 종목 수를 계산한다. TOP3은 양/음 기여를 분리해 각 방향 최대 3개만 선택한다.
- 기존 `premium_5m()` 계산을 창 범위로 자를 수 있게 최소 인자를 추가하고, 창 시작·종료 NAV와 시장가로 괴리 변화를 계산한다.
- 공시·뉴스·수급 후보는 `available_at <= window_end`를 강제하고 실제 인용한 ID/필드/시각을 근거 묶음에 기록한다.
- 공시와 뉴스가 모두 비면 명세의 부재 고지를 코드가 생성한다. 조회 실패는 부재로 바꾸지 않는다.

수용 기준:
- 창 밖 구성종목 봉과 `window_end` 이후 자료는 계산·LLM 입력·출력·근거에 없다.
- TOP3 방향 분리, 폭, NAV 항등식이 원천 수치와 일치한다.
- 화면 블록과 저장 근거 ID가 일대일로 대조된다.

검증:
- 창 경계 봉, 동일 시각 PIT, 종료 후 자료 배제 테스트.
- 상승/하락 TOP3과 NAV 임계 이하 생략/초과 발생 테스트.

### 3. LLM 자리표시자 계약과 블록 조립

대상:
- `src/apps/cloud/analysis-engine/src/edge_analysis/statics/plain.py`
- `src/apps/cloud/analysis-engine/src/edge_analysis/statics/etfcell.py`
- `src/apps/cloud/analysis-engine/tests/statics/test_narrate.py`
- `src/apps/cloud/analysis-engine/tests/statics/test_etfcell.py`

변경:
- `BlockPlan`이 허용한 자리표시자 목록과 블록별 근거 ID만 LLM에 전달한다.
- LLM 응답에서 허용되지 않은 숫자·시각·종목명·출처·자리표시자를 거부한다. 실제 값 치환은 검증 후 코드가 수행한다.
- 기본 수치 모드와 요약 모드는 같은 `WindowFacts`를 사용한다. 요약 모드는 화면 수치만 정성 문구로 바꾸고 원 계산값은 계보/로그에 유지한다.
- 기존 산문 가드(방향·basis·bundle ID)를 재사용한다. 새 병렬 서사 경로는 만들지 않는다.

수용 기준:
- 모델이 임의 수치·시각·이름을 써도 출력되지 않는다.
- 비어 있는 조건부 블록을 모델이 만들 수 없다.
- 출력 블록은 명세 순서이며 숫자와 근거는 코드 값과 동일하다.

검증:
- 악성/오류 LLM 응답으로 미허용 토큰, 순서 변경, 가짜 블록, 가짜 출처를 각각 거부하는 테스트.
- 수치/요약 모드가 같은 사실·근거·블록 발생 여부를 공유하는 테스트.

### 4. 분봉 파이프라인에 요청창 연결

대상:
- `src/apps/cloud/analysis-engine/src/edge_analysis/pipeline.py`
- `src/apps/cloud/analysis-engine/src/edge_analysis/statics/etfcell.py`
- `src/apps/cloud/analysis-engine/tests/test_pipeline.py`

변경:
- 분봉 실행은 세션 OPEN 원장 시각을 `window_start`, 트리거 5분봉의 확정 시각(`window_start + 5분`)을 `window_end`로 전달한다.
- 일봉 실행은 기존 하루 설명 경로를 유지한다. 요청창 블록 계약은 분봉 설명에만 적용한다.
- 저장 payload에 요청창 좌표, 블록별 근거 묶음, 사용 데이터셋/필드/엔티티/시간/as_of 계보를 함께 전달한다. 기존 스키마 필드로 표현 가능하면 재사용하고, 불가능할 때만 별도 마이그레이션을 추가한다.

수용 기준:
- 같은 분봉 트리거 재실행은 이전 설명을 읽지 않고 독립 결과를 만든다.
- 출력의 기준 시각과 저장된 `as_of`가 트리거 봉 확정 시각과 같다.
- 일봉 경로 동작은 바뀌지 않는다.

검증:
- 분봉 fake store/lake로 window 좌표와 PIT 상한 전달 테스트.
- 분봉 파이프라인 smoke: 요청창 출력에 이전 창 문장과 `갱신됨`/`supersedes`가 없고 결과가 저장된다.

### 5. 전체 검증과 문서 정합성

검증:
- 변경 파일 `ruff check`.
- `pytest tests/statics/test_interval.py tests/statics/test_premium5.py tests/statics/test_evidence.py tests/statics/test_narrate.py tests/statics/test_etfcell.py tests/test_pipeline.py -q`.
- 전체 `pytest tests -q`.
- `docs-sync`으로 README/설계/스키마 드리프트를 확인하고 실제 변경된 계약만 갱신한다.
- `edge-review`로 Rule 2/3/9/12, PIT, 계보, 블록-근거 일치를 검토하고 발견사항을 반영한다.

## 체크포인트

- 1 완료: 요청창 밖 정보가 사라지고 결정론적 블록 구조가 테스트로 고정됨.
- 2 완료: 모든 계산·근거가 요청창/PIT 상한에 정렬됨.
- 3 완료: LLM이 문장 외 사실을 만들 수 없음.
- 4 완료: 실제 분봉 호출과 저장 계보가 새 계약을 사용함.
- 5 완료: 전체 테스트·문서·리뷰 통과.
