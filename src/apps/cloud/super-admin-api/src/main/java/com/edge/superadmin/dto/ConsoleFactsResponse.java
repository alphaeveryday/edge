package com.edge.superadmin.dto;

import com.edge.superadmin.repository.ConsoleFactsRepository.BoundaryRow;
import com.edge.superadmin.repository.ConsoleFactsRepository.ChainFeed;
import com.edge.superadmin.repository.ConsoleFactsRepository.ChainRow;
import com.edge.superadmin.repository.ConsoleFactsRepository.ChainStage;
import com.edge.superadmin.repository.ConsoleFactsRepository.OutputRow;
import com.edge.superadmin.repository.ConsoleFactsRepository.RunRow;
import com.edge.superadmin.repository.RunControlPlane;
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
 * "계측 없음(필드 부재)"으로 바뀌어, 콘솔이 없애려는 칸 혼동을 서버가 다시 만든다.
 *
 * <p>원장 축은 전부 찼다 — <b>조회 창 + 런 축 + 작업 축 + 데이터셋 축 + 산출 축 + 경계 축 +
 * 체인 축</b>. 축을 하나씩 더하는 동안 지킨 규약이 이것이었다: 빈 배열은 "봤는데 없었다"이고
 * <b>필드 부재는 "아직 안 본다"</b>라 규칙 층이 그 둘을 다르게 센다.
 *
 * <p>여기에 <b>제어면 축</b>이 붙었다(ALPHA-979 조각 2) — {@code runs[].awsStatus}·
 * {@code awsStop}·{@code meta.aws}. 원장이 아니라 SFN 을 물어야 나오는 사실이고,
 * <b>원장 값으로 폴백하지 않는다</b>: 못 봤으면 {@code null} 이다.
 *
 * <p>아직 부재하는 축은 {@code queues[]} 하나다(조각 3 — SQS). 그래서 규칙 R12 가 <b>못 돎</b>
 * 으로 선다(ADR-0050).
 *
 * <p>표시 문자열을 만들지 않는다 — 건수·시각·판정 코드를 raw 로 내리고 포맷은 UI 소관이다
 * ({@link SourceReportResponse} 와 같은 규약).
 */
public record ConsoleFactsResponse(List<RunResponse> runs, List<TaskResponse> tasks,
		List<DatasetResponse> datasets, List<OutputResponse> outputs, BoundaryResponse boundary,
		ChainResponse chain, MetaResponse meta) {

	/**
	 * 런 하나. {@code id} 는 {@code run_key} 다 — 사건 식별자의 대상 축이라 내부 id 를 쓰면
	 * 다른 축과 조인이 끊긴다.
	 *
	 * <p>시각·날짜는 ISO 문자열로 내린다. 표시 형식을 만들지 않는 것이 이 응답의 규약이다.
	 *
	 * <p>{@code planned}·{@code noRunRow} 는 <b>런 행이 없는 계획 슬롯</b>에만 실린다. 실재 런에
	 * 대해 "스케줄 상 있어야 할 슬롯인가"를 답하는 계측이 없어서 {@code null} 을 내보내지 않고
	 * <b>필드를 뺀다</b> — {@code false} 로 채우면 모름이 "계획된 적 없다"는 단정으로 뒤집힌다.
	 * 여기가 이 응답에서 {@code @JsonInclude} 를 <b>필드 단위로</b> 거는 유일한 자리다.
	 */
	public record RunResponse(String id, String lane, String tradingDate, String ledgerStatus,
			String ledgerUpdated, String deadline,
			@JsonInclude(JsonInclude.Include.NON_NULL) Boolean planned,
			@JsonInclude(JsonInclude.Include.NON_NULL) Boolean noRunRow,
			String awsStatus, String awsStop) {

		/**
		 * @param aws 이 런의 제어면 관측. <b>{@code null} 이면 못 봤다</b> — 원장 값으로 폴백하지
		 *            않는다. 두 축을 합치는 순간 대조가 무의미해진다(ALPHA-979 조각 2 · R03).
		 */
		public static RunResponse from(RunRow r, RunControlPlane.RunState aws) {
			return new RunResponse(r.runKey(), r.lane(), iso(r.tradingDate()), r.ledgerStatus(),
					iso(r.ledgerUpdated()), iso(r.deadline()), r.planned(), r.noRunRow(),
					aws == null ? null : aws.status(), aws == null ? null : iso(aws.stopAt()));
		}
	}

	/**
	 * 작업 하나. {@code runId} 는 런의 {@code id}(=run_key)와 <b>같은 축</b>이다 — 사건을 런에
	 * 매다는 값이라 내부 {@code pipeline_run_id} 를 쓰면 와이어에서 런 축과 안 이어진다.
	 *
	 * <p>{@code TaskRow} 의 뒤쪽 여섯 컬럼(계약·신선도)은 <b>여기 없다</b>. 그건 데이터셋 축을
	 * 파생하는 재료이지 작업 축의 사실이 아니다 — {@link DatasetResponse} 가 그 축이다.
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
	 * 데이터셋 하나. 이 축은 원장 테이블이 아니라 <b>작업에서 파생</b>한다({@code dataset_contract}
	 * 테이블이 없어 계약·신선도가 {@code ops_expected_task} 의 컬럼으로 산다).
	 *
	 * <p>{@code id} 는 {@code ops_expected_task.dataset} 이다 — 작업 축의 {@code dataset} 과 같은
	 * 축이라 소비자가 둘을 잇는다.
	 *
	 * <p>{@code unverifiable} 은 판정 <b>코드</b>지 문장이 아니다(포맷은 UI 소관). null 이면
	 * "신선도를 판정할 수 있다"는 뜻이고, 그 판정 자체는 여기서 하지 않는다 — 규칙은 클라이언트다.
	 */
	public record DatasetResponse(String id, boolean contract, String expectedAsOf,
			String actualAsOf, String collectedAt, String unverifiable) {
	}

	/**
	 * 산출 하나 — 그 날의 값과 <b>평소</b>(직전 거래일 중앙값).
	 *
	 * <p>🔴 <b>{@code today} 와 {@code base} 의 nullability 가 다른 것이 이 타입의 전부다.</b>
	 * {@code today} 는 {@code long} 이라 0 이 <b>실측</b>이고, {@code base} 는 {@code Double} 이라
	 * <b>null 이 "비교할 평소가 없다"</b>이다. 기준을 0 으로 메우면 소비자가 그 산출을 −100% 로
	 * 판정한다 — 휴장일의 장 산출이 정확히 그 자리다.
	 *
	 * <p>{@code label}·{@code unit} 은 소비자 어휘와 1:1 이고, 표시 문자열은 만들지 않는다
	 * (건수는 raw 로 내리고 포맷은 UI 소관 — 이 응답의 규약).
	 */
	public record OutputResponse(String id, String label, long today, Double base, String unit) {

		public static OutputResponse from(OutputRow o) {
			return new OutputResponse(o.id(), o.label(), o.today(), o.base(), o.unit());
		}
	}

	/**
	 * 게시 경계의 정합 — 게시 상태와 테넌트 발번이 <b>어긋난 건수</b>.
	 *
	 * <p>⚠️ <b>배열이 아니라 객체 하나다</b>. 다른 축은 "그 날의 행들"이지만 이건 <b>지금 어긋난
	 * 것이 몇 건인가</b>라 수 셋으로 끝난다. 그리고 이 축만 <b>날짜 창을 안 탄다</b> — 누적이라
	 * 어제 어긋난 것이 오늘 저절로 낫지 않는다.
	 *
	 * <p>{@code deliveryRows} 는 <b>"발번이 돌고는 있나"</b>를 답한다: 앞의 둘이 0 일 때 그것이
	 * <b>정합</b>인지 <b>발번이 아직 하나도 없음</b>인지 이 값이 가른다. 셋 다 실측이라
	 * {@code long} 이고 null 이 없다.
	 *
	 * <p>⚠️ <b>비율의 분모로는 못 쓴다</b> — 앞의 둘은 단위가 서로 다르고(결과 건수 vs 테넌트별
	 * 발번 건수) {@code deliveryRows} 에는 {@code INVALIDATION} 도 들어간다.
	 */
	public record BoundaryResponse(long publishedWithoutDelivery, long deliveryNowNonpublished,
			long deliveryRows) {

		public static BoundaryResponse from(BoundaryRow b) {
			return new BoundaryResponse(b.publishedWithoutDelivery(), b.deliveryNowNonpublished(),
					b.deliveryRows());
		}
	}

	/**
	 * 설명 생산 체인 — 그 날 발화한 트리거가 단계마다 몇 건 남았나(ALPHA-979).
	 *
	 * <p><b>두 목록의 순서가 계약이다.</b> 소비자는 {@code feeds} 를 각 갈래의 첫 점으로 삼아
	 * {@code stages} 를 순서대로 인접 비교한다 — 순서를 재배치하면 아무것도 안 깨진 채 손실 판정만
	 * 뒤섞인다. {@code feeds[0]} 이 배치, {@code feeds[1]} 이 장중이다(위치로 읽는다).
	 *
	 * <p>⚠️ <b>이 축이 없는 응답과 값이 0 인 응답은 다르다.</b> 축 부재는 "안 물어봤다"이고 0 은
	 * "코호트를 따라갔더니 그 단계에 아무도 도달 못 했다"는 <b>실측</b>이다. 그래서 수는 전부
	 * {@code long} 이고 이 축에는 {@code null} 자리가 없다.
	 *
	 * <p>⚠️ <b>0 자체가 위반인 것은 아니다.</b> 소비자(R10)는 인접한 두 값의 <b>감소</b>를 보므로,
	 * 앞 단계도 0 이면 아무 위반도 안 선다(그 갈래는 애초에 아무것도 안 흘렀다). 그렇게 적었다가
	 * 정정했다 — "0 이면 손실"은 이 응답이 하지 않는 판정이다.
	 */
	public record ChainResponse(List<ChainFeedResponse> feeds, List<ChainStageResponse> stages) {

		public static ChainResponse from(ChainRow c) {
			return new ChainResponse(c.feeds().stream().map(ChainFeedResponse::from).toList(),
					c.stages().stream().map(ChainStageResponse::from).toList());
		}
	}

	/** 입력 한 갈래. {@code unit} 이 갈래마다 다르다 — 배치는 ETF 종수, 장중은 발화 건수다. */
	public record ChainFeedResponse(String id, String label, long v, String unit, String src) {

		public static ChainFeedResponse from(ChainFeed f) {
			return new ChainFeedResponse(f.id(), f.label(), f.v(), f.unit(), f.src());
		}
	}

	/**
	 * 단계 하나 — 두 갈래를 나란히 낸다. 갈래는 {@code etf_contribution_observation} 의 트리거 FK
	 * 하나가 가르고, 그 아래 단계에는 배치/장중을 가르는 컬럼이 <b>없다</b>(관측에서 상속한다).
	 */
	public record ChainStageResponse(String id, String label, long batch, long intraday,
			String src) {

		public static ChainStageResponse from(ChainStage s) {
			return new ChainStageResponse(s.id(), s.label(), s.batch(), s.intraday(), s.src());
		}
	}

	/**
	 * {@code today} 는 실제로 조회한 날 — 요청이 date 를 생략했을 때 무엇을 본 응답인가.
	 * <b>거래일이라는 보장은 없다</b>(계획만 있던 날·원장이 빈 경우의 KST 오늘).
	 *
	 * <p>{@code aws} 는 <b>제어면을 언제 봤는가</b>다(ALPHA-979 조각 2). 부재가 두 형상이고 뜻이
	 * 다르다 — <b>키가 없으면</b> 이 축을 안 싣던 배포본이고, <b>키가 있고 {@code null} 이면</b>
	 * 물어봤는데 못 봤다(권한·장애). 화면이 그 둘을 다르게 그린다({@code awsObservation}).
	 * 그래서 이 필드는 <b>{@code null} 이어도 싣는다</b>.
	 */
	public record MetaResponse(String db, String today, String aws) {
	}

	private static String iso(OffsetDateTime at) {
		return at == null ? null : at.toString();
	}

	private static String iso(LocalDate date) {
		return date == null ? null : date.toString();
	}
}
