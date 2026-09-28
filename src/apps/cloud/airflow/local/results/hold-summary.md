# 실행 상태 불명 시 보류 — 로컬 검증 결과(최종 코드, 2026-09-28)

실 PostgreSQL 16(dev와 같은 68개 마이그레이션)·실 업무 코드·실 Airflow 3.3.2 scheduler, ECS·SNS는 대역(`fake_aws.py`). 판정은 exit code가 아니라 **업무 실행 수**(스텝 함수 호출)·**canonical 파티션 쓰기**·**ECS 태스크·RunTask 요청 수**로 했다. 기대와 다르면 시나리오가 실패로 끝난다. 코드: `results/raw/final-code-state.txt`(커밋 안 함).

## scenario-hold — 보류 정책(16)

| 확인 | 결과 |
|---|---|
| V1 lock 을 얻은 B 는 업무 0(다른 슬롯·같은 슬롯) | 통과 |
| V1 A 종료 뒤 다음 실행은 정상 | 통과 |
| V1b 강제 종료된 시도가 열린 동안 보류, ECS 종료 확인 뒤 1회 | 통과 |
| V2 응답 유실: 재전송은 같은 토큰, 태스크·업무 1 | 통과 |
| V2b 추적 불가 → 보류, 새 태스크 없음, 원장 ECS 보류 | 통과 |
| V7a clear 는 보류를 우회하지 못함 | 통과 |
| V7b 재처리 run 도 업무 0 | 통과 |
| V7c 운영자 해제 뒤 1회 실행 | 통과 |
| V3 첫 시도 조회 실패: 제출 0·보류 아님 | 통과 |
| V3 둘째 시도 조회 실패: 제출 0·보류·원장 기록·정제 미시작 | 통과 |
| V3b 해제 전 다음 슬롯 수집도 보류(업무 0·외부 호출 0) | 통과 |
| V5a 도는 수집에 재접속 — 새 태스크·외부 호출 0 | 통과 |
| V5b 끝난 성공을 재사용 — 새 태스크·외부 호출 0 | 통과 |
| V6 기동 실패 확정 뒤에만 새 태스크, 업무 1회 | 통과 |
| V8a 다른 슬롯 동시 시작 — 직렬 | 통과 |
| V8b 정기 run·재처리 run 동시 요청 — 직렬 | 통과 |

합계: 통과 16 · 실패 0

## scenario-unsettled — 결말 없는 실행(10)

| 확인 | 결과 |
|---|---|
| U1 수동 failed → 보류 원장 기록, 하류 skip, verdict 보류 | 통과 |
| U1 보류 중 다른 슬롯·재처리 정제 업무 0(표시된 태스크만 1회 완료) | 통과 |
| U1 보류 중 clear 도 업무 0 | 통과 |
| U1 해제 뒤 재처리 정상 1회 | 통과 |
| U2 worker 사망(마지막 시도) → 보류 원장 기록, 새 태스크 0, 업무 1 | 통과 |
| U3 DAG 시간 초과 → report 없이 주기 점검이 원장 밖 태스크를 보류로 기록 | 통과 |
| U3 늦게 뜬 원장 밖 태스크·다른 슬롯 수집 모두 업무 0·외부 호출 0 | 통과 |
| U3 해제 뒤 다음 수집 정상 | 통과 |
| U4 옛 run 의 늦은 보고가 새 run 판정을 덮지 않음 | 통과 |
| U5 외부 종료: Airflow RESULT_UNKNOWN = 원장 stopped_result_unknown | 통과 |

합계: 통과 10 · 실패 0

## 회귀

- 기존 SFN 경로(`scenario-legacy`)·정상 Airflow 경로(`scenario-airflow`) 10슬롯: canonical이 dev와 바이트 동일, DB 3518행 값 sha `d5ff3270a090`, 외부 호출 3206 — 이전 기록과 같다(`summary.md`). 대역 SFN은 #958 이전의 배포 ASL 사본(`inputs/deployed-asl.json`)을 해석한다.

## 실패 기록(raw, 커밋 안 함)

- scenario-hold: round1(판정 단언 없음)·round2(하네스 대기 결함)·round3(실행 중 코드 수정)·round4(코드 수정으로 중단)·round5(시나리오 순서 — V3 보류 미해제로 V5 차단, 정책대로)·리뷰 수정 전 중단·브랜치 전환 뒤 끊긴 bind mount.
- scenario-unsettled: r1(함수 이름이 전역 변수에 가려짐)·r2(초기화가 DAG 삭제 전에 대역을 비움 — 초기화 순서 수정)·리뷰 수정 전 중단·끊긴 bind mount.

## 대역이 입증하지 못하는 것

ECS clientToken 보존 시간, ListTasks 반영 지연, 멈춘 태스크 보존 시간, 실제 stopCode·exit, 주기 Reconciler의 실제 IAM — README "실제 환경 검증 계획".
