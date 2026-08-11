package com.edge.superadmin.repository;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

/**
 * 슈퍼 어드민 콘솔 규칙 엔진이 읽는 <b>사실</b>(ALPHA-738 · docs/contracts/console-facts-api.md).
 *
 * <p>여기서 위반을 판정하지 않는다 — 규칙은 프론트의 순수 함수이고 이 인터페이스는 그 입력만
 * 낸다. 원장 어휘({@code orchestration_status}·{@code task_outcome} 등)를 다섯 번째 어휘로
 * 뭉개지 않는 것도 {@link PipelineStatusRepository} 와 같다.
 *
 * <p><b>계측이 없는 축은 이 인터페이스에 없다</b>(계약 §부재를 싣는 규약 — "필드를 안 보낸다").
 * 축은 조각별로 하나씩 붙었고 <b>조회 창 + 런 축(계획 결손 슬롯 포함) + 작업 축 + 산출 축 +
 * 경계 축</b>으로 <b>전부 찼다</b>.
 *
 * <p>와이어의 <b>데이터셋 축은 여기 없다</b> — 원장에 그 테이블이 없어 작업에서 파생하고, 파생은
 * {@code ConsoleFactsService} 소관이다. 이 인터페이스는 그 재료({@link TaskRow} 뒤쪽 여섯 컬럼)만 낸다.
 */
public interface ConsoleFactsRepository {

	/**
	 * 하루치 사실. {@code date} 가 null 이면 원장이 아는 <b>가장 최근 날</b>이다 — 런이 있던
	 * 마지막 날과 계획만 있던 마지막 날 중 뒤쪽이고, <b>거래일이라는 보장은 없다</b>.
	 *
	 * <p>축이 붙으면 그 조회들이 <b>한 스냅샷에서</b> 돌아야 한다 — 런을 읽은 뒤 writer 가
	 * 커밋하고 작업을 읽으면 "런은 SUCCEEDED 인데 작업은 PENDING" 같은, 어느 시점에도 존재하지
	 * 않은 조합이 한 판정 위에 조립된다(드릴다운 네 조회와 같은 이유).
	 */
	ConsoleFacts facts(LocalDate date);

	/** {@code today} 는 실제로 조회한 날 — 요청이 생략됐을 때 무엇을 봤는지 화면이 알아야 한다. */
	record ConsoleFacts(LocalDate today, OffsetDateTime dbNow, List<RunRow> runs,
			List<TaskRow> tasks, List<OutputRow> outputs, BoundaryRow boundary) {
	}

	/**
	 * 게시 경계의 정합 — <b>게시 상태와 테넌트 발번이 어긋난 건수</b>.
	 *
	 * <p>⚠️ 이 축만 <b>날짜 창을 안 탄다.</b> 나머지 축은 "그 날 무슨 일이 있었나"이고 이건
	 * "지금 어긋난 것이 몇 건인가"라 <b>누적</b>이다 — 어제 어긋난 것이 오늘 저절로 낫지 않는다.
	 * 날짜로 자르면 조회한 날에 안 생긴 위반이 화면에서 사라진다.
	 *
	 * <p>{@code deliveryRows} 는 분모다 — 앞의 둘이 0 일 때 그것이 <b>정합</b>인지 <b>발번이 아직
	 * 하나도 없음</b>인지 가른다.
	 */
	record BoundaryRow(long publishedWithoutDelivery, long deliveryNowNonpublished,
			long deliveryRows) {
	}

	/**
	 * 산출 하나 — 그 날의 값과 <b>평소</b>(직전 거래일 중앙값).
	 *
	 * <p>{@code today} 는 {@code long} 이다: 그 날 0건인 것은 <b>실측</b>이지 모름이 아니다.
	 * 반대로 {@code base} 는 {@code Double} 이라 <b>null 이 곧 "비교할 평소가 없다"</b>이고,
	 * 소비자는 그런 산출을 편차 판정에서 뺀다. 두 축의 nullability 가 다른 것이 이 record 의 핵심이다.
	 */
	record OutputRow(String id, String label, String unit, long today, Double base) {
	}

	/**
	 * 런 하나. {@code runKey} 가 곧 사건 식별자의 대상 축이다 — 내부 {@code pipeline_run_id} 가
	 * 아니라 이 값이라야 다른 축(작업 등)이 붙을 때 조인이 선다.
	 *
	 * <p>{@code planned}·{@code noRunRow} 는 <b>런 행이 없는 계획 슬롯</b>에만 채워진다
	 * ({@code ops_reconciliation_issue PLANNER_MISSING}). 실재하는 런 행에는 {@code null} 이다 —
	 * "스케줄 상 있어야 할 슬롯인가"를 답하는 계측이 원장에 없기 때문이고(크론 설정은 DB 밖),
	 * 없는 것을 {@code false} 로 채우면 모름이 "계획된 적 없다"는 단정으로 뒤집힌다.
	 */
	record RunRow(String runKey, String lane, LocalDate tradingDate, String ledgerStatus,
			OffsetDateTime ledgerUpdated, OffsetDateTime deadline, Boolean planned,
			Boolean noRunRow) {
	}

	/**
	 * 작업 하나. 뒤쪽 여섯 컬럼({@code datasetContractKey}~{@code freshnessReason})은 와이어에
	 * <b>작업 축으로 나가지 않는다</b> — {@code ConsoleFactsService} 가 <b>데이터셋 축을 파생</b>
	 * 하는 재료다(계약·신선도는 별도 테이블이 아니라 {@code ops_expected_task} 의 컬럼이다).
	 */
	record TaskRow(String taskKey, String runKey, String pipelineType, LocalDate tradingDate,
			String stage, String dataset, boolean required, String planStatus, String taskOutcome,
			String dataStatus, Long recordsOut, Long failedRecords, Long completenessExpected,
			Long completenessReceived, Long completenessMissing, long attempts,
			String datasetContractKey, LocalDate expectedAsOf, LocalDate actualAsOf,
			OffsetDateTime collectedAt, String freshnessStatus, String freshnessReason) {
	}
}
