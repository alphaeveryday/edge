package com.edge.superadmin.repository;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

/**
 * 운영 원장(`ops_*`) **읽기 전용** 리포지토리 — 데이터 소스 수집 상태 화면의 데이터원(ALPHA-514,
 * 실행 드릴다운 ALPHA-574).
 *
 * <p>원장의 소유 모듈은 data-pipeline 이다(ADR-0005 단일 writer). 이 콘솔은 조회만 하며 어떤
 * 상태도 쓰지 않는다 — 원장은 실행을 제어하지 않는 관측 projection 이고, 콘솔은 그 projection 의
 * 독자일 뿐이다.
 *
 * <p>JPA 엔티티를 두지 않는 이유: {@code ddl-auto: validate} 환경에서 원장 5테이블을 매핑하면
 * 소유하지도 않는 스키마에 이 앱의 기동이 묶인다. 읽기 집계 하나라 JdbcTemplate 이면 충분하다.
 *
 * <p>인터페이스로 두는 것은 standalone 컨트롤러 테스트가 손 페이크로 대체하기 위함이다
 * (레포 hand-fake 관례, Mockito 미도입).
 */
public interface PipelineStatusRepository {

	/** 가장 최근에 계획된 파이프라인 런의 상태. 원장에 런이 하나도 없으면 empty. */
	Optional<PipelineRunStatus> latestRun();

	/**
	 * 슬롯 키로 지목한 런 하나. 그런 런이 없으면 empty — <b>빈 원장과 구분된다</b>.
	 *
	 * <p>주소로 쓰는 값이 {@code run_key} 인 이유: 이미 화면에 떠 있어 운영자가 복사할 수 있는
	 * 유일한 식별자이고, 스키마에 UNIQUE 제약이 있으며, 내부 ID 를 새로 노출하지 않는다.
	 */
	Optional<PipelineRunStatus> runByKey(String runKey);

	/**
	 * 최근 {@code days}일 안에 <b>계획된</b> 런 전부와 각 런의 기대 작업 — 실행 격자 화면의
	 * 데이터원(ALPHA-594). 계획 시각 오름차순이라 격자에서 시간이 왼쪽→오른쪽으로 흐른다.
	 *
	 * <p>{@link TaskStatus} 를 재사용하지 않는 이유: 격자는 슬롯 수 × 작업 수(30일이면 수백 셀)라
	 * 시도 전량·시각 4축을 실으면 드릴다운 한 화면 분량이 슬롯 수만큼 곱해진다. 셀이 답할
	 * 질문(계획·귀결·데이터 상태·건수·사유)만 싣고, 나머지는 셀에서 런 드릴다운(ALPHA-574,
	 * {@link #runByKey})으로 넘어가 본다.
	 */
	List<GridSlot> grid(int days);

	/**
	 * 격자 한 열 — 슬롯(런) 하나와 그 런의 기대 작업들.
	 *
	 * <p>{@code launchStatus} 를 함께 싣는 이유는 드릴다운과 같다 — <b>기동 실패는 orchestration
	 * 이 영영 null 이라</b> 이 축이 없으면 "아예 못 뜬 슬롯"이 격자에서 "상태 없음"으로 보인다.
	 *
	 * <p>{@code tasks} 가 빈 런도 슬롯으로 낸다 — 기대 작업이 안 적힌 런(기동 실패 등)을 열에서
	 * 빼면 부재가 화면에서 사라지는데, 부재야말로 이 원장이 답하려는 질문이다.
	 */
	record GridSlot(String runKey, String launchStatus, String orchestrationStatus,
			LocalDate tradingDate, List<GridCell> tasks) {
	}

	/**
	 * 격자 셀 하나 — 한 슬롯에서 한 작업의 관측 상태. 축을 합치지 않는 이유는
	 * {@link TaskStatus} 와 같고, {@code recordsOut}·{@code failedRecords} 의 null 계약(모름 ≠ 0)도
	 * 같다(ALPHA-182).
	 *
	 * <p>{@code running} — <b>귀결이 아직 없는데(PENDING) 도는 물리 시도가 있는가</b>. outcome 은
	 * wrapper 가 끝날 때 써서 실행 중엔 PENDING 이라, 이 축이 없으면 런이 도는 내내 "돌고 있다"와
	 * "아직 시작도 안 했다"가 같은 셀이 된다(수집 상태 화면이 executionStatus 를 싣는 이유와 같다).
	 *
	 * <p>PENDING 조건을 거는 이유: RUNNING 시도의 <b>존재만</b> 보면, 강제 종료로 RUNNING 인 채
	 * 남은 죽은 시도가 이미 판정 끝난 셀을 <b>영구히</b> "실행 중"으로 만든다 — 드릴다운은 같은
	 * 화면의 STALLED 이슈 표가 그 잔재를 드러내지만 격자엔 그 장치가 없다. 귀결이 적히는 순간
	 * 이 신호가 걷히므로 오표시가 유계다. 대가로 "판정 후 재시도 중"(FAILED+새 시도)은 격자에선
	 * 안 보인다 — 그 정밀도는 시도 전량을 싣는 드릴다운(574) 소관이다.
	 */
	record GridCell(String stage, String taskKey, String planStatus, String outcome,
			String dataStatus, Long recordsOut, Long failedRecords, String skipReason,
			String outcomeReason, boolean running) {
	}

	/**
	 * 런 하나와 그 런의 기대 작업들, 그리고 이 런에 걸린 대조 이슈들.
	 *
	 * @param runKey             슬롯 멱등키(예: {@code etf-daily:2026-07-27T15:40}) — 화면의 "언제 런"
	 * @param launchStatus       Planner 의 SFN 기동 결과(LAUNCHED·LAUNCH_FAILED·…). <b>기동 실패는
	 *                           orchestration 이 영영 null 이라</b> 이 축이 없으면 "아예 시작 못 함"이
	 *                           화면에서 "표시할 상태 없음"으로 사라진다 — 원장이 답하려는 바로 그
	 *                           질문("원래 실행돼야 했는데 시작되지 않은 것")이다
	 * @param orchestrationStatus SFN 실행 귀결(RUNNING·SUCCEEDED·FAILED·…). null 이면 아직 미확인
	 * @param tradingDate        대상 거래일. 비거래일 SKIP 판정의 근거라 화면에 함께 낸다
	 * @param issues             Reconciler 가 이 런에 연 불일치. <b>원장이 이미 판정해 둔 것을 콘솔이
	 *                           그동안 한 건도 보여주지 않았다</b> — 화면에 없으면 운영자에게는
	 *                           없는 사실이다(dev 의 거짓 LEDGER_GAP 17건이 그렇게 묻혀 있었다)
	 */
	record PipelineRunStatus(String runKey, String launchStatus, String orchestrationStatus,
			LocalDate tradingDate, List<TaskStatus> tasks, List<IssueStatus> issues) {
	}

	/**
	 * 기대 작업 한 건의 관측 상태.
	 *
	 * <p>{@code planStatus}(DUE·SKIPPED)와 {@code outcome}(PENDING·FULFILLED·FAILED·BLOCKED·MISSED)은
	 * <b>다른 축</b>이라 합치지 않는다 — "원래 할 일이었나"와 "그래서 어떻게 됐나"는 운영자가
	 * 각각 알아야 하고, 하나로 뭉개면 SKIPPED(휴장이라 안 함)와 FULFILLED(해서 됐음)가 같은
	 * 초록이 된다.
	 *
	 * <p>{@code dataStatus}(UNKNOWN·VALID·VALID_EMPTY·INCOMPLETE·INVALID)는 실행 성패와 <b>또 다른
	 * 축</b>이다 — 실행이 성공(FULFILLED)해도 산출 데이터는 INCOMPLETE 일 수 있다. 이 축을 빼면
	 * 데이터가 불완전한 작업이 화면에서 온전히 초록으로 보인다(Rule 12).
	 *
	 * <p>{@code recordsOut}·{@code failedRecords}는 <b>null 이 정상값</b>이다(ALPHA-182) — 신호가
	 * 없거나 못 믿을 값이면 0 으로 메우지 않는다. 0 으로 내리면 화면에서 "0건 처리"와 "모름"이
	 * 구분되지 않는다.
	 *
	 * <p>{@code completeness}는 행 건수가 아니라 기대·수신 <b>개체 수</b>다(ALPHA-611).
	 * 아직 배선되지 않은 작업은 객체 자체가 null 이고, 기대값만 고정됐지만 수신 신호를 못 얻은
	 * 작업은 객체 안의 {@code received}·{@code missing}이 null 이다. 콘솔은 이 값을 다시 계산하지
	 * 않고 원장에 기록된 사실 그대로 읽는다.
	 *
	 * <p>시각 넷은 <b>서로 다른 질문</b>에 답한다: {@code expectedAt}(언제 하기로 했나) ·
	 * {@code deadlineAt}(언제까지였나) · {@code missedAt}(언제 못 했다고 판정했나) ·
	 * {@code fulfilledAt}(언제 됐나). {@code missedAt} 은 비래치라 나중에 FULFILLED 로 가도 남는다 —
	 * "늦게라도 됐다"와 "제때 됐다"가 outcome 만으로는 같은 값이기 때문이다.
	 *
	 * <p>{@code skipReason}·{@code outcomeReason}은 <b>"왜"가 저장되는 유일한 자리</b>다. 특히
	 * {@code FAILED_TO_START} 는 attempt 행 자체가 없어(ECS ARN 없는 submit 실패는 원장에 가짜 행을
	 * 남기지 않는다) 이 필드가 없으면 화면이 그 실패의 이유를 영영 못 보여준다.
	 *
	 * <p>{@code attempts}는 기록 시각 오름차순이다. 전량을 싣는 이유는 재시도 이력과
	 * {@code RECONCILER_BACKFILL}(사후 복구) 구분이 마지막 한 건만 보면 사라지기 때문이다.
	 * "지금 상태를 말해주는 시도"는 순서가 아니라 {@link #currentAttempt()} 가 정한다.
	 *
	 * @param currentAttemptId 원장이 <b>스스로 지목한</b> "현재 결과와 연결된 시도"
	 *                         ({@code ops_expected_task.current_attempt_id}). 화면에 내보내지 않는
	 *                         내부 ID 이고, {@link #currentAttempt()} 의 근거로만 쓴다
	 */
	record TaskStatus(String stage, String taskKey, String dataset, String planStatus,
			String outcome, String dataStatus, Long recordsOut, Long failedRecords,
			CompletenessStatus completeness,
			OffsetDateTime expectedAt, OffsetDateTime deadlineAt, OffsetDateTime missedAt,
			OffsetDateTime fulfilledAt, String skipReason, String outcomeReason,
			List<AttemptStatus> attempts, String currentAttemptId) {

		/**
		 * 지금 상태를 말해주는 시도. 시도가 없으면 null.
		 *
		 * <p><b>원장이 실행 순서를 완전히 알지는 못한다</b>는 전제에서 출발한다. 사후 복구는 실제
		 * 실행 시각을 모른 채 {@code started_at = now()} 로 기록하고(`ledger.py`
		 * {@code backfill_attempt}), Reconciler 는 복구 후 {@code task_outcome} 만 갱신할 뿐
		 * {@code current_attempt_id} 는 건드리지 않는다(`reconciler.py` {@code _judge_outcome}).
		 * 그래서 <b>시각으로 골라도(복구된 옛 시도가 맨 뒤로 온다) 지목으로 골라도(지목이 낡을 수
		 * 있다) 각각 틀리는 경우가 있다</b> — 어느 쪽이 옛 실행인지 가릴 정보가 원장에 없다.
		 *
		 * <p>그래서 <b>확실한 것부터</b> 고른다:
		 * <ol>
		 *   <li>RUNNING 인 시도가 있으면 그중 <b>마지막</b> 것. 이 자리에 지목을 쓸 수 없다 —
		 *       {@code current_attempt_id} 는 wrapper 가 작업이 <b>끝날 때</b> 쓴다(`wrapper.py` 의
		 *       두 {@code update_task_outcome} 호출이 모두 {@code run_fn()} 뒤에 있다). 그래서
		 *       재시도가 도는 동안 지목은 여전히 <b>직전 시도</b>를 가리키고, 지목을 먼저 보면
		 *       운영자가 화면을 보는 바로 그 순간(런이 도는 중)에 "지금 돌고 있다"가 사라진다.</li>
		 *   <li>없으면 원장이 지목한 것({@code current_attempt_id}) — 유일하게 <b>선언된</b> 답이라
		 *       추측보다 낫다. 콘솔이 판정을 새로 만들지 않는다는 이 화면의 원칙 그대로다.</li>
		 *   <li>지목도 없으면(사후 복구만 있는 작업 등) 순서상 마지막.</li>
		 * </ol>
		 *
		 * <p>남는 한계 둘 — 없는 정보를 지어내는 대신 적어 둔다(Rule 12):
		 * <ul>
		 *   <li><b>죽은 RUNNING</b>: 프로세스가 강제 종료돼 attempt 가 RUNNING 으로 남으면 이미
		 *       끝난 작업이 "실행 중"으로 보인다. wrapper 는 이걸 피하려 {@code BaseException} 까지
		 *       잡아 attempt 를 끝내므로 정상 경로에선 생기지 않고, 생기면 Reconciler 가 STALLED
		 *       이슈를 연다 — 그 이슈는 이제 같은 화면의 이슈 표에 함께 뜬다.</li>
		 *   <li><b>낡은 지목 + 사후 복구</b>: 이 값의 시각이 뒤처질 수 있다. 그때도 작업의 귀결은
		 *       {@code outcome}(Reconciler 가 최신 증거로 갱신)이 정답이고, 화면은 시도 전량을 함께
		 *       그리므로 운영자가 대조할 수 있다.</li>
		 * </ul>
		 *
		 * <p>"현재 시도"의 정의를 <b>여기 한 곳</b>에만 둔다 — 예전에는 SQL(LATERAL 의 정렬)과
		 * 표시 층이 각자 "마지막"을 정하고 있어 둘이 어긋날 수 있었다.
		 */
		public AttemptStatus currentAttempt() {
			if (attempts.isEmpty()) {
				return null;
			}
			// 뒤에서부터 — RUNNING 이 여럿이면 가장 나중 것이 지금이다(가장 오래된 것을 집으면
			// 죽은 채 남은 옛 시도가 새 실행을 가린다).
			for (AttemptStatus attempt : attempts.reversed()) {
				if ("RUNNING".equals(attempt.executionStatus())) {
					return attempt;
				}
			}
			for (AttemptStatus attempt : attempts) {
				if (attempt.attemptId().equals(currentAttemptId)) {
					return attempt;
				}
			}
			return attempts.getLast();
		}
	}

	/** 기대·수신 개체 수와 그 차이. 세 값 모두 원장의 JSONB 값을 재계산 없이 옮긴다. */
	record CompletenessStatus(Long expected, Long received, Long missing) {
	}

	/**
	 * 물리 실행 시도 하나.
	 *
	 * <p>{@code executionStatus}는 <b>시도의 물리 상태</b>다(RUNNING·SUCCEEDED·…). outcome 은
	 * wrapper 가 <b>끝날 때</b> 써서 실행 중엔 PENDING 으로 남는데, 그것만 보면 "돌고 있다"와 "아직
	 * 시작도 안 했다"가 같은 값이 된다 — 원장이 답하려는 질문이 바로 후자라 둘을 갈라야 한다.
	 *
	 * <p>{@code recordSource}는 정상 계측(WRAPPER)과 사후 복구(RECONCILER_BACKFILL)를 가른다.
	 * 뭉개면 "원장이 스스로 메운 행"이 "실제로 관측된 실행"처럼 보인다 — 원장이 관대해지는 쪽이다.
	 *
	 * <p>{@code exitCode}는 <b>박스 타입</b>이다. 0 은 성공이고 null 은 모름인데 원시 타입으로
	 * 받으면 JDBC 가 SQL NULL 을 0 으로 돌려줘 <b>모름이 성공으로 뒤집힌다</b>(ALPHA-182 NULL 계약).
	 */
	record AttemptStatus(String attemptId, Integer attemptNumber, String ecsTaskArn,
			String executionStatus, OffsetDateTime startedAt, OffsetDateTime finishedAt,
			Integer exitCode, String failureReason, String recordSource) {
	}

	/**
	 * Reconciler 가 연 예정↔실제 불일치 한 건.
	 *
	 * @param scope   run·task·slot — 무엇에 대한 불일치인가
	 * @param taskKey scope 가 task 일 때 그 작업의 카탈로그 키. 그 외에는 null (내부 ID 를 화면에
	 *                흘리지 않고 운영자가 아는 이름으로 바꿔 낸다)
	 * @param status  OPEN·RESOLVED. 해결된 것도 함께 내린다 — 지난 런의 드릴다운에서 "그때 무슨
	 *                일이 있었나"는 해결 여부와 별개로 이력이다
	 */
	record IssueStatus(String issueType, String scope, String taskKey, String status,
			int occurrenceCount, OffsetDateTime firstSeenAt, OffsetDateTime lastSeenAt,
			String resolutionReason) {
	}

	/**
	 * 레인(pipeline_type)별 <b>최신 런</b>과 그 런의 기대 작업 — Run Overview 첫 화면의
	 * 데이터원(ALPHA-683). 격자·드릴다운과 달리 "지금"만 본다.
	 */
	List<OverviewLane> overview();

	/**
	 * 레인 하나의 최신 런. 격자와 같은 이유로 작업이 안 적힌 런(기동 실패 등)도 낸다 —
	 * 부재가 1급 신호다.
	 */
	record OverviewLane(String pipelineType, String runKey, String launchStatus,
			String orchestrationStatus, LocalDate tradingDate, List<OverviewTask> tasks) {
	}

	/**
	 * Overview 가 판정에 쓰는 작업 축만 실은 행. {@link TaskStatus} 를 재사용하지 않는 이유는
	 * 격자 셀과 같다 — 시도 전량·시각 4축이 필요 없다. 대신 여기만 {@code required} 와
	 * freshness 축을 싣는다: 필수 여부는 "오늘 발행 가능한가" 판정의 분모이고(ALPHA-683),
	 * freshness 는 원장 컬럼(ADR-0043)을 재계산 없이 옮긴다 — writer(ALPHA-654) 배선 전엔
	 * 전부 null 이 정상이다.
	 */
	record OverviewTask(String stage, String taskKey, String planStatus, String outcome,
			String dataStatus, boolean required, OffsetDateTime deadlineAt, Long failedRecords,
			String freshnessStatus, LocalDate expectedAsOfDate, LocalDate actualAsOfDate) {
	}
}
