# ADR-0028: 파이프라인 SFN 통합 — 4페이즈와 feature/분석 경계

- 상태: 승인됨
- 날짜: 2026-07-17

## 맥락

수집(SFN `edge-dev-data-pipeline-raw-ingest`, raw→normalize→derive)과 분석(SFN
`edge-dev-analysis-engine-analyze`, 단일 ECS 태스크)이 별도 상태머신·별도 스케줄로 돌았다.
두 스케줄 사이에 순서 보장이 없어 분석이 그날 feature 적재 전에 돌 수 있고, "raw 수집"이라는
SFN 이름이 실제 범위(정제·파생 포함)와 어긋났다. 한편 팀 경계(ADR-0026: 정준영=로직·알고리즘,
김진기=파이프라인 구현·인프라·DB 적재)가 feature 산출 레이어에서 만나는데, 그 레이어가
분석엔진 내부에 섞여 있어 협업 단위가 불분명했다.

장기적으로는 수집 빈도가 줄어도 분석은 **가격이벤트 기반 비동기**로 돌 수 있어야 한다 —
그러려면 "분석이 무엇을 읽는가"가 지금 계약으로 고정돼야 한다.

## 결정

- 상태머신을 하나로 통합한다: `edge-dev-data-pipeline`(접미사 없음), **raw 수집 → 정제/정규화
  → feature/factor 생성 → 분석**의 4페이즈. 각 페이즈는 전량 성공 게이트로 순서를 보장한다.
- **feature 페이즈**(구 derive 개명)의 최종 범위는 뉴스/공시 assertion·event·event_thread 추출과
  가격이벤트(price_movement_trigger) 생성까지다. 추출 로직은 alphamale 에서 data-pipeline
  이미지로 이관해 이 페이즈의 SFN 스텝으로 편입한다(후속 티켓, 정준영과 합의).
- **분석 페이즈 계약**: 분석은 feature 산출물만 읽는다(canonical/feature 존 + Cloud Event Store 의
  price_movement_trigger·instrument). 실행은 당분간 날짜 동기(오늘)이며, 비동기 전환 시 이
  페이즈만 가격이벤트 트리거 기반으로 떼어낸다.
- analysis-engine 전용 terraform 모듈·SFN·스케줄은 삭제하고 data-pipeline 모듈로 흡수한다.
  이미지는 분리 유지(alphamale 코드베이스) — 로직/구현 경계는 이미지 경계다.
- 특정일(trade_date) 수동 분석 재실행은 SFN 입력 라우팅 대신 `aws ecs run-task` 레시피로 한다.

## 대안

- **분석 SFN 유지 + startExecution.sync 체인**: trade_date 수동 실행 계약이 공짜로 유지되고
  비동기 전환 시 체인만 끊으면 되지만, 상태머신·알람·스케줄 2벌 유지비가 남는다. 수동 재실행
  빈도가 낮아 run-task 레시피로 충분하다고 판단, 단일 상태머신을 택했다.
- **추출 로직을 분석엔진 내부에 유지**: SFN 통합만 하고 feature 분리를 미루는 안. 협업 경계가
  계속 불분명하고 비동기 전환 시 분석엔진을 쪼개야 하는 빚이 남아 기각.

## 결과

- 순서 보장: 분석은 항상 그 실행의 feature 적재 뒤에 돈다. 실패 알림·타임아웃 알람도 한 벌.
- SFN 이름 변경은 destroy+recreate 지만 무상태라 안전. 스케줄은 data-pipeline 것 하나로
  통합되며(미 동부 16:10, DISABLED) 컷오버 전 재검토는 ALPHA-387 소관.
- 분석의 SFN 타임아웃 예산(21600초)이 4페이즈 공유가 된다 — 초과는 `execution_timed_out`
  알람이 감지한다.
- 따라오는 의무: ① assertion/event/event_thread 추출의 data-pipeline 이관(정준영 합의) ②
  price trigger 이중 writer 정리(파이프라인 0.5% vs 분석엔진 내부 L0 3% — 단일 writer 확정)
  ③ 컷오버 시 통합 스케줄 시각 재설계(ALPHA-387).
