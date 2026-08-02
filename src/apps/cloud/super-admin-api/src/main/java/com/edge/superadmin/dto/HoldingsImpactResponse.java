package com.edge.superadmin.dto;

import com.edge.superadmin.repository.HoldingsImpactRepository.AffectedAnalysis;
import com.edge.superadmin.repository.HoldingsImpactRepository.Impact;
import com.edge.superadmin.repository.HoldingsImpactRepository.MissingEtf;

import java.util.List;

/**
 * KRX holdings 결손 영향 응답(ALPHA-686). 단위: 기대·적재·누락은 <b>ETF 종</b>,
 * analyses 는 <b>설명 결과 건</b>.
 *
 * <p>{@code snapshotMissing} = 기대 목록 부재로 영향 범위 계산 불가(UNKNOWN) — 빈 누락
 * 목록과 <b>다르다</b>(스펙 §6.3: UNKNOWN ≠ 영향 없음). {@code recommendedAction} 은 권장
 * 재실행 명령 문자열이다 — 자동 실행 없음(스펙 §12 안전정책: 대시보드는 권장만).
 *
 * <p>누락의 원인(수집/정제/적재 중 어디서 탈락)은 여기서 단정하지 않는다 — 그 분해는 S3
 * 로그 소관이고, 이 응답은 "기대에 있었는데 기준일 적재분에 지금 없다"는 사실까지만 말한다. {@code loadPending}(이 기준일 대상 적재 중 미귀결 존재 — 기준일 축)이면 결손은 잠정이고 권고도 내지 않는다.
 */
public record HoldingsImpactResponse(String runKey, String expectedAsOf,
		Integer expectedCount, Integer loadedCount, boolean snapshotMissing,
		boolean loadPending, List<MissingEtfResponse> missing, String recommendedAction) {

	/**
	 * {@code instrumentId} null = instrument 행 자체가 없음(프로필 수집까지 결손) — 단축코드만
	 * 표시 가능. {@code analyses} 빈 목록은 "기준일 분석 없음"이라는 사실이지 결손의 결과라고
	 * 단정하지 않는다(트리거 미발동 정상 무분석과 구분 불가 — 오귀인 금지).
	 */
	public record MissingEtfResponse(String ourEtfId, String instrumentId, String etfName,
			List<AnalysisResponse> analyses) {

		static MissingEtfResponse from(MissingEtf m) {
			return new MissingEtfResponse(m.ourEtfId(), m.instrumentId(), m.etfName(),
					m.analyses().stream().map(AnalysisResponse::from).toList());
		}
	}

	public record AnalysisResponse(String explanationResultId, String explanationRunId,
			String publicationStatus, String summary) {

		static AnalysisResponse from(AffectedAnalysis a) {
			return new AnalysisResponse(a.explanationResultId(), a.explanationRunId(),
					a.publicationStatus(), a.summary());
		}
	}

	public static HoldingsImpactResponse from(Impact impact, String recommendedAction) {
		return new HoldingsImpactResponse(impact.runKey(),
				impact.expectedAsOf() == null ? null : impact.expectedAsOf().toString(),
				impact.expectedCount(), impact.loadedCount(), impact.snapshotMissing(),
				impact.loadPending(),
				impact.missing().stream().map(MissingEtfResponse::from).toList(),
				recommendedAction);
	}

	/** 원장에 etf-daily 런이 하나도 없음 — 초기 환경의 정상 상태(에러 아님). */
	public static HoldingsImpactResponse empty() {
		return new HoldingsImpactResponse(null, null, null, null, true, false, List.of(), null);
	}
}
