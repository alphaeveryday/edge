package com.edge.superadmin.repository;

import java.util.Collections;
import java.util.EnumMap;
import java.util.Map;
import java.util.function.Supplier;

/**
 * 콘솔 사실 조회({@code GET /api/v1/console/facts})가 시간을 쓰는 <b>구간</b>(ALPHA-1314). 응답의
 * {@code meta.timing.segments} 가 이 목록을 <b>선언 순서대로, 전부</b> 싣는다 — 구간의 정의는 여기
 * 한 곳이다.
 *
 * <p>구간은 응답의 축 단위이고 <b>서로 겹치지 않는다</b>. 한 축이 DB 와 AWS 를 함께 쓰면 둘로 가른다
 * (런 축: {@link #RUNS_DB}·{@link #RUNS_AWS}). 순서는 실행 순서다.
 *
 * <p>⚠️ 재기만 한다. 구간을 더하거나 빼면 응답의 계약이 바뀐다 — 테스트가 이름 목록을 고정하고
 * 계약 문서({@code docs/contracts/console-facts-api.md} 의 「소요 시간」)가 같은 목록을 적는다.
 */
public enum FactsSegment {
	/** 조회 시각·KST 오늘·최신 런 날짜, 날짜를 생략한 요청이면 기본 조회일을 고르는 조회까지 */
	META("meta", Kind.DB),
	/** 런과 계획 결손 슬롯 */
	RUNS_DB("runs.db", Kind.DB),
	TASKS("tasks", Kind.DB),
	/** 휴장일·기준일 조회와 산출별 건수 조회 전부 */
	OUTPUTS("outputs", Kind.DB),
	BOUNDARY("boundary", Kind.DB),
	/** 체인의 입력 두 갈래({@code chain.feeds}) — 배치 트리거 수와 장중 발화 수 */
	FEEDS("feeds", Kind.DB),
	/** 새 분석 엔진의 단계({@code chain.analysisStages}) */
	ANALYSIS_STAGES("analysisStages", Kind.DB),
	ANALYSIS_FAILURES("analysisFailures", Kind.DB),
	ETF_ANALYSES("etfAnalyses", Kind.DB),
	/**
	 * 원장 조회 호출 전체에서 위 DB 구간의 합을 <b>뺀 나머지</b> — 커넥션을 얻고 트랜잭션을 열고 닫는
	 * 시간과, 구간 사이의 메모리 작업이다. 따로 잰 것이 아니라 뺄셈이다.
	 */
	TRANSACTION("transaction", Kind.DB),
	/** 런의 Step Functions 실행 상태 조회 — 트랜잭션 밖이다 */
	RUNS_AWS("runs.aws", Kind.AWS),
	/** 큐 상태 조회(SQS) — 트랜잭션 밖이다 */
	QUEUES("queues", Kind.AWS),
	/** 작업에서 데이터셋 축을 접는 메모리 작업 — 조회가 없다 */
	DATASETS("datasets", Kind.APP);

	/** 구간이 시간을 쓰는 곳. 응답에는 소문자로 나간다. */
	public enum Kind { DB, AWS, APP }

	private final String wireName;
	private final Kind kind;

	FactsSegment(String wireName, Kind kind) {
		this.wireName = wireName;
		this.kind = kind;
	}

	public String wireName() {
		return wireName;
	}

	public Kind kind() {
		return kind;
	}

	/**
	 * 요청 하나의 구간별 소요 시간(나노초). 단조 시계({@link System#nanoTime})로 잰다. 같은 구간을 두
	 * 번 재면 더한다. 요청마다 새로 만들고 스레드 사이에 나누지 않는다.
	 */
	public static final class Timer {

		private final EnumMap<FactsSegment, Long> nanos = new EnumMap<>(FactsSegment.class);

		/** {@code body} 를 그대로 실행하고 걸린 시간을 {@code segment} 에 더한다. 예외는 그대로 올린다. */
		public <T> T time(FactsSegment segment, Supplier<T> body) {
			long started = System.nanoTime();
			try {
				return body.get();
			}
			finally {
				add(segment, System.nanoTime() - started);
			}
		}

		public void add(FactsSegment segment, long elapsedNanos) {
			nanos.merge(segment, elapsedNanos, Long::sum);
		}

		/** 잰 구간만, 선언 순서로. */
		public Map<FactsSegment, Long> nanos() {
			return Collections.unmodifiableMap(new EnumMap<>(nanos));
		}
	}
}
