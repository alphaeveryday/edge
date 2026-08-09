package com.edge.superadmin.dto;

import com.edge.superadmin.repository.ConsoleFactsRepository.BoundaryRow;
import com.edge.superadmin.repository.ConsoleFactsRepository.OutputRow;
import com.edge.superadmin.repository.ConsoleFactsRepository.RunRow;
import com.edge.superadmin.repository.ConsoleFactsRepository.TaskRow;
import com.fasterxml.jackson.annotation.JsonInclude;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

/**
 * 콘솔 규칙 엔진의 사실 응답(ALPHA-738 · docs/contracts/console-facts-api.md).
 *
 * <p>필드명은 UI 타입과 같은 camelCase(기존 콘솔 API 관례). 원장 record 와 형식이 같아도 와이어
 * 형은 별도 타입으로 둔다.
 *
 * <p><b>부재를 싣는 규약이 이 타입의 전부다.</b> 실측 0 은 {@code 0}, 집계 없음·관측 불가는
 * {@code null}, <b>계측 없음은 필드 자체를 두지 않는다</b>. 그래서 클래스 단위
 * {@code @JsonInclude} 를 걸지 않는다 — NON_NULL 을 위에 걸면 "집계 없음(null)"이 조용히
 * "계측 없음(필드 부재)"으로 바뀌어, 콘솔이 없애려는 칸 혼동을 서버가 다시 만든다. 필드 단위로
 * 필요한 곳에만 붙인다({@link RunResponse#planned}).
 *
 * <p>이 응답에 <b>없는</b> 축과 그 이유:
 * <ul>
 *   <li>{@code chain}·{@code queues}·{@code etfLedger}·{@code runbook} — 계측 없음.
 *       규칙은 축 부재를 {@code canRun} 으로 읽어 "위반 0건"이 아니라 <b>못 돎</b> 으로 센다.</li>
 *   <li>{@code runs[].awsStatus}·{@code runs[].awsStop}·{@code meta.aws} — AWS 제어면 미배선(C 축).
 *       배선되면 {@code awsUnavailable}·{@code meta.awsUnobservedRuns} 와 함께 들어온다.</li>
 *   <li>{@code runs[].kind} — <b>AWS 와 무관하다.</b> 런 종류를 쓰는 writer 가 아예 없어 계측
 *       자체가 없다. C 축을 붙여도 이 필드는 안 생긴다.</li>
 *   <li>{@code tasks[].maxRetries}·{@code lastOk}·{@code okRate} — 정책 상한과 최근 이력 집계가
 *       원장에 없다.</li>
 *   <li>{@code boundary.syncCursorRows} — {@code tenant_sync_cursor} 에 쓰는 코드가 0건이다.
 *       행 수 0 은 "한 번도 pull 안 했다"가 아니라 <b>기록하지 않음</b> 이라 셀 값이 아니다.</li>
 * </ul>
 *
 * <p>표시 문자열을 만들지 않는다 — 건수·시각·판정 코드를 raw 로 내리고 포맷은 UI 소관이다
 * ({@link SourceReportResponse} 와 같은 규약).
 */
public record ConsoleFactsResponse(List<RunResponse> runs, List<TaskResponse> tasks,
		List<DatasetResponse> datasets, List<OutputResponse> outputs, BoundaryResponse boundary,
		MetaResponse meta) {

	/**
	 * {@code planned}·{@code noRunRow} 는 <b>런 행이 없는 슬롯</b>에만 실린다. 실재 런에 대해
	 * "스케줄 상 있어야 할 슬롯인가"를 답하는 계측이 없어서 {@code null} 을 내보내지 않고
	 * 필드를 뺀다 — {@code false} 로 채우면 모름이 "계획된 적 없다"는 단정으로 뒤집힌다.
	 */
	public record RunResponse(String id, String lane, String tradingDate, String ledgerStatus,
			String ledgerUpdated, String deadline,
			@JsonInclude(JsonInclude.Include.NON_NULL) Boolean planned,
			@JsonInclude(JsonInclude.Include.NON_NULL) Boolean noRunRow) {

		public static RunResponse from(RunRow r) {
			return new RunResponse(r.runKey(), r.lane(), iso(r.tradingDate()), r.ledgerStatus(),
					iso(r.ledgerUpdated()), iso(r.deadline()), r.planned(), r.noRunRow());
		}
	}

	/**
	 * {@code runId} 는 런의 {@code id}(=run_key)와 같은 축이다 — 사건을 런에 매다는 값이라
	 * 내부 {@code pipeline_run_id} 를 쓰면 런 축과 조인이 안 된다.
	 */
	public record TaskResponse(String taskKey, String runId, String pipelineType,
			String tradingDate, String stage, String dataset, boolean required, String planStatus,
			String taskOutcome, String dataStatus, Long recordsOut, Long failedRecords,
			Long completenessExpected, Long completenessReceived, Long completenessMissing,
			long attempts) {

		public static TaskResponse from(TaskRow t) {
			return new TaskResponse(t.taskKey(), t.runKey(), t.pipelineType(),
					iso(t.tradingDate()), t.stage(), t.dataset(), t.required(), t.planStatus(),
					t.taskOutcome(), t.dataStatus(), t.recordsOut(), t.failedRecords(),
					t.completenessExpected(), t.completenessReceived(), t.completenessMissing(),
					t.attempts());
		}
	}

	/**
	 * 데이터셋 축은 작업에서 <b>파생</b>한다({@code dataset_contract} 테이블은 없다).
	 *
	 * <p>{@code unverifiable} 은 판정 <b>코드</b>다 — 문장이 아니다(포맷은 UI 소관).
	 */
	public record DatasetResponse(String id, boolean contract, String expectedAsOf,
			String actualAsOf, String collectedAt, String unverifiable) {
	}

	/** {@code base} 가 null 이면 기준 없음 — 그 산출은 편차 판정 대상이 아니다. */
	public record OutputResponse(String id, String label, long today, Double base, String unit) {

		public static OutputResponse from(OutputRow o) {
			return new OutputResponse(o.id(), o.label(), o.today(), o.base(), o.unit());
		}
	}

	public record BoundaryResponse(long publishedWithoutDelivery, long deliveryNowNonpublished,
			long deliveryRows) {

		public static BoundaryResponse from(BoundaryRow b) {
			return new BoundaryResponse(b.publishedWithoutDelivery(), b.deliveryNowNonpublished(),
					b.deliveryRows());
		}
	}

	/** {@code today} 는 실제로 조회한 날 — 요청이 date 를 생략했을 때 무엇을 본 응답인가.
	 *  <b>거래일이라는 보장은 없다</b>(계획만 있던 날·원장이 빈 경우의 KST 오늘). */
	public record MetaResponse(String db, String today) {
	}

	private static String iso(OffsetDateTime at) {
		return at == null ? null : at.toString();
	}

	private static String iso(LocalDate date) {
		return date == null ? null : date.toString();
	}
}
