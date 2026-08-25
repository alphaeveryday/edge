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
 * 축은 조각별로 하나씩 붙었다 — <b>조회 창 + 런 축(계획 결손 슬롯 포함) + 작업 축 + 산출 축 +
 * 경계 축 + 체인 축 + ETF 귀결 축</b>. AWS 상태와 큐는 원장이 아니라 제어면을 물어 얻는다.
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

	/**
	 * {@code date} 이하의 최근 엔티티 해소 관측 10개. 같은 날 여러 뉴스 런이 있으면
	 * <b>마지막으로 갱신된 성공 관측 하나</b>만 남고, 반환 순서는 오래된 날부터다.
	 *
	 * <p>{@code date} 가 null 이면 DB 시계의 KST 오늘까지 본다. 과거 행을 백필하지 않으므로
	 * 배선 전 날짜는 점을 합성하지 않고 목록에서 빠진다.
	 */
	List<EntityResolutionPoint> entityResolutionTrend(LocalDate date);

	/** 분모가 0 일 수 있어 비율은 이 원천 형이 아니라 와이어 매핑에서 nullable 로 계산한다. */
	record EntityResolutionPoint(LocalDate date, long totalArguments, long resolvedArguments) {
	}

	/**
	 * {@code maxDate} 로 끝나는 장중 분석 코호트의 연속 {@code days}일 사실.
	 * 날짜가 비어도 0인 점을 내며, 반환 순서는 오래된 날부터다.
	 */
	IntradayAnalysisTrend intradayAnalysisTrend(LocalDate maxDate, int days);

	record IntradayAnalysisTrend(OffsetDateTime asOf, List<IntradayAnalysisPoint> points) {
	}

	/** 단계 수는 행 수가 아니라 그 단계에 도달한 장중 관측 수라 재실행에도 단조성을 지킨다. */
	record IntradayAnalysisPoint(LocalDate date, long triggers, long observations, long runs,
			long activeRuns, long failedRuns, long results, long published) {
	}

	/** {@code today} 는 실제로 조회한 날 — 요청이 생략됐을 때 무엇을 봤는지 화면이 알아야 한다. */
	record ConsoleFacts(LocalDate today, OffsetDateTime dbNow, List<RunRow> runs,
			List<TaskRow> tasks, List<OutputRow> outputs, BoundaryRow boundary, ChainRow chain,
			List<EtfAnalysisRow> etfLedger) {
	}

	/** 조회일에 발화한 ETF별 <b>최신 트리거</b>의 최신 설명 실행 상태(R15). */
	record EtfAnalysisRow(String etf, String name, String outcome) {
	}

	/**
	 * 설명 생산 체인의 <b>그 날 코호트</b>(ALPHA-979) — 그 날 발화한 트리거가 단계마다 몇 건
	 * 남았나. 두 목록의 <b>순서가 곧 흐름</b>이다: 소비자는 인접한 두 값을 비교해 감소를 손실로
	 * 읽는다(R10). 원장에는 단계 사이의 선후가 없어 순서를 복원할 방법이 없다.
	 *
	 * <p>수는 전부 {@code long} 이다 — <b>0 이 실측</b>이라서다. 코호트를 정해 놓고 세므로 "못
	 * 셌다"가 없다. 축 전체가 안 나가는 경우만 부재이고, 그건 이 record 가 {@code null} 인 것이다.
	 *
	 * <p><b>단계가 세는 것은 "그 단계에 도달한 코호트 구성원 수"</b>이지 그 단계의 행 수가 아니다.
	 * 그래서 각 단계는 앞 단계의 부분집합이고, 감소는 언제나 "여기서 멈춘 구성원"을 뜻한다.
	 * 행을 세면 재실행 같은 다중도가 손실로도 은폐로도 읽힌다(구현 주석에 실증 둘).
	 *
	 * <p>흐름은 <b>Cloud 게시에서 끝난다</b> — 발번 이후는 전달 경계의 물음이고
	 * {@link BoundaryRow} 가 답한다(ADR-0026).
	 */
	record ChainRow(List<ChainFeed> feeds, List<ChainStage> stages) {
	}

	/** 체인의 입력 한 갈래. {@code unit} 이 갈래마다 다르다 — 배치는 ETF, 장중은 발화 건이다. */
	record ChainFeed(String id, String label, String unit, String src, long v) {
	}

	/** 단계 하나 — 두 갈래를 나란히 낸다. 갈래를 가르는 것은 관측의 트리거 FK 하나뿐이다. */
	record ChainStage(String id, String label, String src, long batch, long intraday) {
	}

	/**
	 * 게시 경계의 정합 — <b>게시 상태와 테넌트 발번이 어긋난 건수</b>.
	 *
	 * <p>⚠️ 이 축만 <b>날짜 창을 안 탄다.</b> 나머지 축은 "그 날 무슨 일이 있었나"이고 이건
	 * "지금 어긋난 것이 몇 건인가"라 <b>누적</b>이다 — 어제 어긋난 것이 오늘 저절로 낫지 않는다.
	 * 날짜로 자르면 조회한 날에 안 생긴 위반이 화면에서 사라진다.
	 *
	 * <p>{@code deliveryRows} 는 <b>"발번이 돌고는 있나"</b>를 답한다 — 앞의 둘이 0 일 때 그것이
	 * <b>정합</b>인지 <b>발번이 아직 하나도 없음</b>인지 가른다. ⚠️ 비율의 분모로는 못 쓴다:
	 * 앞의 둘은 단위가 다르고 여기엔 {@code INVALIDATION} 도 들어간다.
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
	 *
	 * <p>{@code executionArn} 은 <b>와이어에 안 나간다</b> — 제어면에 물을 때 쓰는 내부 locator 다
	 * (ALPHA-979 조각 2). {@code sfn_execution_arn}(확인된 것)이 있으면 그것, 없으면
	 * {@code expected_execution_arn}(Planner 가 계산한 것)이다. ⚠️ 후자의 <b>존재는 실행의 증거가
	 * 아니다</b>(스키마 주석) — 그래서 이 값으로 "실행이 있다"를 말하지 않고, 오직 물어보는 데만 쓴다.
	 * 답이 "그런 실행 없음"이면 그것이 관측 결과다.
	 */
	record RunRow(String runKey, String lane, LocalDate tradingDate, String ledgerStatus,
			OffsetDateTime ledgerUpdated, OffsetDateTime deadline, Boolean planned,
			Boolean noRunRow, String executionArn) {
	}

	/**
	 * 작업 하나. 뒤쪽 여섯 컬럼({@code datasetContractKey}~{@code freshnessReason})은 와이어에
	 * <b>작업 축으로 나가지 않는다</b> — {@code ConsoleFactsService} 가 <b>데이터셋 축을 파생</b>
	 * 하는 재료다(계약·신선도는 별도 테이블이 아니라 {@code ops_expected_task} 의 컬럼이다).
	 */
	record TaskRow(String taskKey, String runKey, String pipelineType, LocalDate tradingDate,
			String stage, String dataset, boolean required, String planStatus, String taskOutcome,
			String dataStatus, Long recordsOut, Long unsupportedRecords, Long failedRecords,
			Long completenessExpected,
			Long completenessReceived, Long completenessMissing, long attempts,
			String datasetContractKey, LocalDate expectedAsOf, LocalDate actualAsOf,
			OffsetDateTime collectedAt, String freshnessStatus, String freshnessReason) {
	}
}
