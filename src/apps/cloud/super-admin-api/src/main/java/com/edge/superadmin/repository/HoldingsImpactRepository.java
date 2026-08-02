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
	 * @param expectedAsOf   계약이 해석한 기준 거래일(expected_as_of_date) — 적재·분석 조인의 시간 축
	 * @param expectedCount  기대 ETF 수(snapshot). 계산 불가면 null
	 * @param loadedCount    기대 ∩ 기준일 적재 — run_id 스코프가 아니다(적재는 read-merge-
	 *                       overwrite 멱등이라 무변경 행의 data_version 이 안 바뀐다). 교집합이라
	 *                       expected = loaded + missing 산술이 성립한다. 계산 불가면 null
	 * @param snapshotMissing 기대 목록 <b>또는 기준일</b> 부재로 계산 불가(UNKNOWN — 영향 없음 아님)
	 * @param loadPending    이 기준일을 대상으로 한 적재 중 미귀결 존재 — loaded/missing 과 같은
	 *                       기준일 축이다(선택한 런의 outcome 이 아니다 — 리뷰 2라운드). true 면
	 *                       결손은 잠정이고 복구 권고를 내지 않는다
	 */
	record Impact(String runKey, LocalDate expectedAsOf, Integer expectedCount,
			Integer loadedCount, boolean snapshotMissing, boolean loadPending,
			List<MissingEtf> missing) {
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

	/** {@code explanationRunId} 는 분석 상세 화면({@code /analyses/:id})의 키다 — 1:1(UNIQUE). */
	record AffectedAnalysis(String explanationResultId, String explanationRunId,
			String publicationStatus, String summary) {
	}
}
