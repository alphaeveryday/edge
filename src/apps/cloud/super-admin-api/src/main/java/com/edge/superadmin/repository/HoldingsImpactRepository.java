package com.edge.superadmin.repository;

import java.time.LocalDate;
import java.util.List;

/**
 * KRX holdings 결손 → 영향 ETF → 당일 분석 전파(ALPHA-686, 판정 스펙 §6 첫 슬라이스).
 *
 * <p><b>"결손"까지만 말한다.</b> 기대(Planner snapshot)와 적재(etf_holding_snapshot)의 차집합은
 * 수집 실패·정제 탈락·적재 탈락의 <b>합</b>이다 — 원인 분해는 S3 로그에만 있어 여기서 단정하지
 * 않는다(first divergence 는 수집 봉투의 completeness 카운트가 따로 말한다).
 *
 * <p>영향 범위를 계산할 수 없으면(기대 snapshot 부재) {@code snapshotMissing} 으로 드러낸다 —
 * 스펙 §6.3: UNKNOWN 을 영향 없음으로 해석하지 않는다.
 */
public interface HoldingsImpactRepository {

	/** @param runKey null 이면 최신 etf-daily 런. 그런 런이 없으면 null 반환(빈 원장). */
	Impact impact(String runKey);

	/**
	 * @param expectedAsOf   계약이 해석한 기준 거래일(expected_as_of_date) — 분석 조인의 시간 축
	 * @param expectedCount  기대 ETF 수(snapshot). snapshot 부재면 null
	 * @param loadedCount    이 런(data_version=run_id)이 적재한 ETF 수
	 * @param snapshotMissing 기대 목록 자체가 없어 영향 범위 계산 불가(UNKNOWN — 영향 없음 아님)
	 */
	record Impact(String runKey, LocalDate expectedAsOf, Integer expectedCount,
			Integer loadedCount, boolean snapshotMissing, List<MissingEtf> missing) {
	}

	/**
	 * 누락 ETF 하나와 그 ETF 의 기준일 분석. {@code instrumentId} null = instrument 행 자체가
	 * 없음(프로필 수집까지 결손된 신규 ETF — 단축코드만 보여줄 수 있다). {@code analyses} 빈
	 * 목록은 "이 ETF 의 기준일 분석이 없다"는 사실이지 결손의 결과라고 단정하지 않는다 —
	 * 트리거가 안 걸린 정상 무분석과 구분할 수 없다(오귀인 금지).
	 */
	record MissingEtf(String ourEtfId, String instrumentId, String etfName,
			List<AffectedAnalysis> analyses) {
	}

	record AffectedAnalysis(String explanationResultId, String publicationStatus,
			String summary) {
	}
}
