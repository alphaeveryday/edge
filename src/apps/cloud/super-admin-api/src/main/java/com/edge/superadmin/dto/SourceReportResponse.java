package com.edge.superadmin.dto;

import com.edge.superadmin.repository.PipelineStatusRepository.AttemptStatus;
import com.edge.superadmin.repository.PipelineStatusRepository.CompletenessStatus;
import com.edge.superadmin.repository.PipelineStatusRepository.IssueStatus;
import com.edge.superadmin.repository.PipelineStatusRepository.PipelineRunStatus;
import com.edge.superadmin.repository.PipelineStatusRepository.TaskStatus;

import java.time.OffsetDateTime;
import java.util.List;

/**
 * 데이터 소스 수집 상태 응답 — 운영 원장의 런 하나를 그대로 노출한다(ALPHA-514, 드릴다운 574).
 *
 * <p>필드는 super-admin-ui sources 타입과 동일한 camelCase. 원장 record 와 형식이 같아도 와이어
 * 형은 별도 타입으로 둔다(기존 관례 유지).
 *
 * <p><b>표시 문자열을 만들지 않는다</b> — 건수·시각을 raw 로 내리고 포맷은 UI 가 한다. 서버가
 * {@code "2,736건"}·{@code "1분 전"} 같은 문자열을 만들면 로캘·상대시각 갱신이 서버에 묶이고,
 * null(신호 없음)을 문자열로 뭉개는 순간 화면이 "0건"과 구분하지 못한다.
 *
 * <p>런이 없으면 {@code run} 이 null, {@code tasks}·{@code issues} 가 빈 배열이다 — 초기 환경의
 * 정상 상태이지 에러가 아니다. 다만 <b>지목한 런이 없는 경우는 여기로 오지 않는다</b>(404).
 *
 * <p>{@code completeness}는 기대·수신 개체 수와 원장이 기록한 차이를 raw 숫자로 싣는다
 * (ALPHA-630). 아직 완전성 미배선인 작업은 객체 자체가 null 이고, 수신 신호가 없으면 객체 안의
 * {@code received}·{@code missing}이 null 이다 — 모름을 0으로 메우거나 API에서 다시 계산하지 않는다.
 */
public record SourceReportResponse(RunResponse run, List<TaskResponse> tasks,
		List<IssueResponse> issues) {

	public record RunResponse(String runKey, String launchStatus, String orchestrationStatus,
			String tradingDate) {
	}

	/**
	 * {@code executionStatus}·{@code lastFinishedAt} 은 별도 조회가 아니라 <b>원장이 지목한 현재
	 * 시도에서 파생</b>한다({@code TaskStatus.currentAttempt()}) — 정의가 두 곳에 있으면 언젠가
	 * 서로 어긋난다. 기존 UI 계약을 유지하려고 와이어에는 그대로 남긴다.
	 */
	public record TaskResponse(String stage, String taskKey, String dataset, String planStatus,
			String outcome, String dataStatus, String executionStatus, Long recordsOut,
			Long unsupportedRecords, Long failedRecords, CompletenessResponse completeness,
			String lastFinishedAt,
			String expectedAt, String deadlineAt,
			String missedAt, String fulfilledAt, String skipReason, String outcomeReason,
			List<AttemptResponse> attempts) {

		public static TaskResponse from(TaskStatus t) {
			AttemptStatus current = t.currentAttempt();
			return new TaskResponse(t.stage(), t.taskKey(), t.dataset(), t.planStatus(),
					t.outcome(), t.dataStatus(),
					current == null ? null : current.executionStatus(),
					t.recordsOut(), t.unsupportedRecords(), t.failedRecords(),
					CompletenessResponse.from(t.completeness()),
					iso(current == null ? null : current.finishedAt()),
					iso(t.expectedAt()), iso(t.deadlineAt()), iso(t.missedAt()),
					iso(t.fulfilledAt()), t.skipReason(), t.outcomeReason(),
					t.attempts().stream().map(AttemptResponse::from).toList());
		}
	}

	/** {@code missing}도 저장된 원장 값이다 — API에서 {@code expected - received}로 만들지 않는다. */
	public record CompletenessResponse(Long expected, Long received, Long missing) {

		private static CompletenessResponse from(CompletenessStatus completeness) {
			return completeness == null ? null : new CompletenessResponse(
					completeness.expected(), completeness.received(), completeness.missing());
		}
	}

	/** {@code attemptId} 는 싣지 않는다 — 내부 ID 이고 화면이 쓸 일이 없다(운영자 어휘가 아니다). */
	public record AttemptResponse(Integer attemptNumber, String ecsTaskArn, String executionStatus,
			String startedAt, String finishedAt, Integer exitCode, String failureReason,
			String recordSource) {

		public static AttemptResponse from(AttemptStatus a) {
			return new AttemptResponse(a.attemptNumber(), a.ecsTaskArn(), a.executionStatus(),
					iso(a.startedAt()), iso(a.finishedAt()), a.exitCode(), a.failureReason(),
					a.recordSource());
		}
	}

	public record IssueResponse(String issueType, String scope, String taskKey, String status,
			int occurrenceCount, String firstSeenAt, String lastSeenAt, String resolutionReason) {

		public static IssueResponse from(IssueStatus i) {
			return new IssueResponse(i.issueType(), i.scope(), i.taskKey(), i.status(),
					i.occurrenceCount(), iso(i.firstSeenAt()), iso(i.lastSeenAt()),
					i.resolutionReason());
		}
	}

	private static String iso(OffsetDateTime at) {
		return at == null ? null : at.toString();
	}

	public static SourceReportResponse empty() {
		return new SourceReportResponse(null, List.of(), List.of());
	}

	public static SourceReportResponse from(PipelineRunStatus run) {
		return new SourceReportResponse(
				new RunResponse(run.runKey(), run.launchStatus(), run.orchestrationStatus(),
						run.tradingDate() == null ? null : run.tradingDate().toString()),
				run.tasks().stream().map(TaskResponse::from).toList(),
				run.issues().stream().map(IssueResponse::from).toList());
	}
}
