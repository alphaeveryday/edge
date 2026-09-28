# 실행 상태 불명 시 보류 — 로컬 검증 결과(`lab.py scenario-hold`, 6회차)

실 PostgreSQL 16·실 업무 코드·실 Airflow 3.3.2 scheduler, ECS·SNS는 대역(`fake_aws.py`). 판정은 exit code가 아니라 **업무 실행 수**(스텝 함수 호출)·**canonical 파티션 쓰기**·**ECS 태스크·RunTask 요청 수**로 했다. 기대와 다르면 시나리오가 실패로 끝난다.

| 확인 | 결과 | 수치 |
|---|---|---|
| V1 lock 을 얻은 B 는 업무 0(다른 슬롯·같은 슬롯) | 통과 |  |
| V1 A 종료 뒤 다음 실행은 정상 | 통과 |  |
| V1b 강제 종료된 시도가 열린 동안 보류, ECS 종료 확인 뒤 1회 | 통과 |  |
| V2 응답 유실: 재전송은 같은 토큰, 태스크·업무 1 | 통과 | {"normalize": {"business": 1, "partition_writes": 1, "ecs_tasks": 1, "run_requests": 5}} |
| V2b 추적 불가 → 보류, 새 태스크 없음, 원장 ECS 보류 | 통과 | {"normalize": {"business": 1, "partition_writes": 1, "ecs_tasks": 1, "run_requests": 10}} |
| V7a clear 는 보류를 우회하지 못함 | 통과 |  |
| V7b 재처리 run 도 업무 0 | 통과 |  |
| V7c 운영자 해제 뒤 1회 실행 | 통과 |  |
| V3 첫 시도 조회 실패: 제출 0·보류 아님 | 통과 |  |
| V3 둘째 시도 조회 실패: 제출 0·보류·원장 기록·정제 미시작 | 통과 |  |
| V3b 해제 전 다음 슬롯 수집도 보류(업무 0·외부 호출 0) | 통과 | {"collect": {"business": 0, "partition_writes": 0, "ecs_tasks": 1, "run_requests": 1}} |
| V5a 도는 수집에 재접속 — 새 태스크·외부 호출 0 | 통과 |  |
| V5b 끝난 성공을 재사용 — 새 태스크·외부 호출 0 | 통과 |  |
| V6 기동 실패 확정 뒤에만 새 태스크, 업무 1회 | 통과 |  |
| V8a 다른 슬롯 동시 시작 — 직렬 | 통과 |  |
| V8b 정기 run·재처리 run 동시 요청 — 직렬 | 통과 |  |

합계: 통과 16 · 실패 0

- 회차 기록(raw, 커밋하지 않음): round1(첫 실행 — 판정 단언 없음), round2(하네스 대기 결함), round3(실행 중 코드 수정 — 무효), round4(코드 수정으로 중단), round5(시나리오 순서: V3 보류 미해제로 V5 차단 — 정책대로 동작, V3b로 편입), round6(이 표, 코드 고정).
- V1b 강제 종료: 대역 StopTask는 exit 137·stopCode UserInitiated 를 남긴다. 실제 ECS 값은 실제 환경 검증 항목이다.
- 대역이 입증하지 못하는 것: ECS clientToken 보존 시간, ListTasks 반영 지연, 멈춘 태스크 보존 시간, 실제 stopCode — README "실제 환경 검증 계획".
