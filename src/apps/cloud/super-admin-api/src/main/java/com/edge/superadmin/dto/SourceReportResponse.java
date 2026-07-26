package com.edge.superadmin.dto;

import com.edge.superadmin.repository.PipelineStatusRepository.PipelineRunStatus;
import com.edge.superadmin.repository.PipelineStatusRepository.TaskStatus;

import java.util.List;

/**
 * 데이터 소스 수집 상태 응답 — 운영 원장의 최신 런을 그대로 노출한다(ALPHA-514).
 *
 * <p>필드는 super-admin-ui sources 타입과 동일한 camelCase. 원장 record 와 형식이 같아도 와이어
 * 형은 별도 타입으로 둔다(기존 관례 유지).
 *
 * <p><b>표시 문자열을 만들지 않는다</b> — 건수·시각을 raw 로 내리고 포맷은 UI 가 한다. 서버가
 * {@code "2,736건"}·{@code "1분 전"} 같은 문자열을 만들면 로캘·상대시각 갱신이 서버에 묶이고,
 * null(신호 없음)을 문자열로 뭉개는 순간 화면이 "0건"과 구분하지 못한다.
 *
 * <p>런이 없으면 {@code run} 이 null, {@code tasks} 가 빈 배열이다 — 초기 환경의 정상 상태이지
 * 에러가 아니다.
 */
public record SourceReportResponse(RunResponse run, List<TaskResponse> tasks) {

	public record RunResponse(String runKey, String launchStatus, String orchestrationStatus,
			String tradingDate) {
	}

	public record TaskResponse(String stage, String taskKey, String dataset, String planStatus,
			String outcome, String dataStatus, Long recordsOut, Long failedRecords,
			String lastFinishedAt) {

		public static TaskResponse from(TaskStatus t) {
			return new TaskResponse(t.stage(), t.taskKey(), t.dataset(), t.planStatus(),
					t.outcome(), t.dataStatus(), t.recordsOut(), t.failedRecords(),
					t.lastFinishedAt() == null ? null : t.lastFinishedAt().toString());
		}
	}

	public static SourceReportResponse empty() {
		return new SourceReportResponse(null, List.of());
	}

	public static SourceReportResponse from(PipelineRunStatus run) {
		return new SourceReportResponse(
				new RunResponse(run.runKey(), run.launchStatus(), run.orchestrationStatus(),
						run.tradingDate() == null ? null : run.tradingDate().toString()),
				run.tasks().stream().map(TaskResponse::from).toList());
	}
}
