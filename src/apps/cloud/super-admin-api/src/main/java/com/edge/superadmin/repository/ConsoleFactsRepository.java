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

	/**
	 * {@code maxDate} 로 끝나는 {@code days}일 동안 v2 분석(전망·가격변동)의 일별 건수(ALPHA-1290).
	 * 날짜는 {@code analysis_at} 의 KST 날짜다. <b>행이 하나도 없는 날짜는 점이 없다</b> — 0 인 점을
	 * 만들지 않는다. 가격변동 분석은 장이 열린 날에만 생기고, v2 표가 생기기 전 날짜에는 값이 없다.
	 * 반환 순서는 종류, 그다음 오래된 날부터다.
	 */
	List<AnalysisDayPoint> analysisTrend(LocalDate maxDate, int days);

	/**
	 * v2 분석 한 종류의 하루. 건수는 분석 행 수다. {@code completedEtfs} 는 그날 완료 행이 하나라도
	 * 있는 ETF 종 수이고, {@code failedEtfs} 는 <b>그날 실패 행이 있고 완료 행이 하나도 없는</b> ETF
	 * 종 수다(ALPHA-1291) — 실패했다가 같은 날 다시 돌아 완료된 ETF 는 넣지 않는다. v2 는 실패한
	 * 분석을 다시 돌려 끝내는 일이 흔해, "실패한 적 있는 ETF"로 세면 거의 매일 0 을 넘는다.
	 * running 만 있는 ETF 도 넣지 않는다.
	 *
	 * <p>{@code running} 은 <b>실패가 아니다</b> — 상태가 running 인 채 남은 행이고, 아직 도는
	 * 중인지 끝나지 못한 것인지 이 표만으로는 가를 수 없다.
	 */
	record AnalysisDayPoint(String kind, LocalDate date, long completed, long failed, long running,
			long completedEtfs, long failedEtfs) {
	}

	record IntradayAnalysisTrend(OffsetDateTime asOf, List<IntradayAnalysisPoint> points) {
	}

	/** 단계 수는 행 수가 아니라 그 단계에 도달한 장중 관측 수라 재실행에도 단조성을 지킨다. */
	record IntradayAnalysisPoint(LocalDate date, long triggers, long observations, long runs,
			long activeRuns, long failedRuns, long results, long published) {
	}

	/** {@code today} 는 실제로 조회한 날 — 요청이 생략됐을 때 무엇을 봤는지 화면이 알아야 한다. */
	record ConsoleFacts(LocalDate today, OffsetDateTime dbNow, List<RunRow> runs,
			List<TaskRow> tasks, List<OutputRow> outputs, BoundaryRow boundary, ChainRow chain,
			List<EtfAnalysisRow> etfLedger, List<AnalysisFailureRow> analysisFailures) {

		/** v2 분석 실패 축 없이 만든다 — 그 축은 빈 목록이다. */
		public ConsoleFacts(LocalDate today, OffsetDateTime dbNow, List<RunRow> runs,
				List<TaskRow> tasks, List<OutputRow> outputs, BoundaryRow boundary, ChainRow chain,
				List<EtfAnalysisRow> etfLedger) {
			this(today, dbNow, runs, tasks, outputs, boundary, chain, etfLedger, List.of());
		}
	}

	/**
	 * 조회일에 v2 분석이 <b>끝내 완료되지 못한</b> ETF 하나(R15, ALPHA-1291) — 그날 그 종류의 분석에
	 * 실패 행이 있고 완료 행이 하나도 없다. {@code kind} 는 {@code outlook}·{@code movement} 다.
	 */
	record AnalysisFailureRow(String kind, String etfCode) {
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
	record ChainRow(List<ChainFeed> feeds, List<ChainStage> stages,
			List<AnalysisStage> analysisStages) {

		/** 새 분석 엔진 단계 없이 만든다 — 그 목록은 비어 있다. */
		public ChainRow(List<ChainFeed> feeds, List<ChainStage> stages) {
			this(feeds, stages, List.of());
		}
	}

	/**
	 * 새 분석 엔진(v2)의 단계 하나(ALPHA-1296) — 조회일에 발화한 장중 트리거 가운데 <b>이 단계에
	 * 도달한 트리거 수</b>다. 단위가 트리거라 한 트리거에 요청·분석이 여럿이어도 한 번만 센다.
	 *
	 * <p>{@code batch} 가 null 이면 <b>대상이 아니다</b> — 배치 트리거는 v2 가 소비하지 않는다(v2 의
	 * 큐 소비자는 장중 발화 이벤트만 받는다). 0 으로 내면 "배치 트리거가 전부 사라졌다"가 된다.
	 *
	 * <p>{@code blind} 는 이 단계를 셀 수 없다는 뜻이다 — 그날 코호트에 v2 요청이 하나도 없고 첫 v2
	 * 요청이 조회일보다 뒤일 때(또는 v2 요청이 아예 없을 때)다. 그때 {@code intraday} 의 0 은 실측이
	 * 아니다.
	 *
	 * <p>{@code pending} 은 <b>아직 판정할 수 없는 트리거 수</b>다(ALPHA-1309) — 앞 단계에는 도달했고
	 * 이 단계에는 아직 못 갔는데, 발화한 지 유예 시간이 안 지난 트리거다. 그 트리거는 {@code intraday}
	 * (도달)에 들어 있지 않고 손실도 아니다. 소비자는 앞 단계와의 차이에서 이 수를 뺀 것만 손실로
	 * 읽는다. 판정하지 않는 단계(분석 완료·게시)는 항상 0 이다.
	 */
	record AnalysisStage(String id, String label, String src, Long batch, long intraday,
			boolean blind, long pending) {

		/** 판정 불가 트리거 없이 만든다 — 그 수는 0 이다. */
		public AnalysisStage(String id, String label, String src, Long batch, long intraday,
				boolean blind) {
			this(id, label, src, batch, intraday, blind, 0L);
		}
	}

	/** 체인의 입력 한 갈래. {@code unit} 이 갈래마다 다르다 — 배치는 ETF, 장중은 발화 건이다. */
	record ChainFeed(String id, String label, String unit, String src, long v) {
	}

	/**
	 * 단계 하나 — 두 갈래를 나란히 낸다. 갈래를 가르는 것은 관측의 트리거 FK 하나뿐이다.
	 *
	 * <p>{@code blind} 는 <b>이 단계를 셀 수 없다</b>는 뜻이다(ALPHA-1293). 단계 다섯은 전부 옛 설명
	 * 엔진의 표에서 세는데, 그 엔진이 조회일보다 앞서 멈췄고 그날 코호트에 행이 하나도 없으면 두 수의
	 * 0 은 "하나도 안 남았다"가 아니라 <b>세는 쪽이 없다</b>이다. 그때 두 수는 0 으로 두되(와이어에
	 * null 자리가 없다) 소비자는 {@code blind} 를 먼저 보고 그 0 을 읽지 않는다.
	 */
	record ChainStage(String id, String label, String src, long batch, long intraday,
			boolean blind) {

		/** 관측 가능한 단계 — 수가 실측이다. */
		public ChainStage(String id, String label, String src, long batch, long intraday) {
			this(id, label, src, batch, intraday, false);
		}
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
	 *
	 * <p>{@code blind} 는 <b>앞의 둘이 지금의 게시를 못 본다</b>는 뜻이다(ALPHA-1293). 두 수는
	 * 옛 설명 엔진의 {@code explanation_result} 와 발번을 맞대는데, 그 엔진이 <b>오늘(KST)보다 앞서</b>
	 * 멈췄으면 그 뒤의 게시(새 분석 엔진이 쓴다)는 이 조인에 안 걸린다. 이 축은 날짜 창을 안 타므로
	 * 기준도 조회일이 아니라 지금이다 — 과거 날짜를 조회해도 표시는 같다. 그때 0 은 "어긋난 것이 없다"가 아니라
	 * "옛 엔진 분에는 없다"이다. 0 이 아닌 수는 여전히 실측이다 — 옛 엔진 분의 어긋남은 그대로 센다.
	 */
	record BoundaryRow(long publishedWithoutDelivery, long deliveryNowNonpublished,
			long deliveryRows, boolean blind) {

		/** 관측 가능한 경계 — 0 이 정합을 뜻한다. */
		public BoundaryRow(long publishedWithoutDelivery, long deliveryNowNonpublished,
				long deliveryRows) {
			this(publishedWithoutDelivery, deliveryNowNonpublished, deliveryRows, false);
		}
	}

	/**
	 * 산출 하나 — 그 날의 값과 <b>평소</b>(직전 거래일 중앙값).
	 *
	 * <p>{@code today} 는 {@code long} 이다: 그 날 0건인 것은 <b>실측</b>이지 모름이 아니다.
	 * 반대로 {@code base} 는 {@code Double} 이라 <b>null 이 곧 "비교할 평소가 없다"</b>이고,
	 * 소비자는 그런 산출을 편차 판정에서 뺀다. 두 축의 nullability 가 다른 것이 이 record 의 핵심이다.
	 *
	 * <p>{@code history} 는 중앙값을 낸 <b>기준일별 건수</b>다(날짜 오름차순, 최대 10일, ALPHA-1289).
	 * <b>조회일은 들어 있지 않다</b> — 조회일 값은 {@code today} 이고, 그날이 미완결이면 아직 쌓이는
	 * 중이라 기준일과 같은 축이 아니다. {@code base} 가 null 인 날에도 이 목록은 실린다: 기준을
	 * 비우는 것은 "이 날을 비교하지 않는다"이지 기준일의 실측이 없다는 뜻이 아니다. 기준일 후보가
	 * 하나도 없으면 빈 목록이다.
	 */
	record OutputRow(String id, String label, String unit, long today, Double base,
			List<OutputDay> history) {

		/** 기준일 목록 없이 만든다 — 그 목록은 빈 목록이다. */
		public OutputRow(String id, String label, String unit, long today, Double base) {
			this(id, label, unit, today, base, List.of());
		}
	}

	/** 기준일 하루의 건수. 0 은 그날 산출이 없었다는 실측이다. */
	record OutputDay(LocalDate date, long count) {
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
