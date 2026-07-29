package com.edge.superadmin.repository;

import java.time.OffsetDateTime;
import java.util.List;

/**
 * analyses 화면이 읽는 설명 원장 조회(ALPHA-601). 트리거(가격 변동)부터 설명 결과까지를
 * 런 단위 한 행으로 낸다 — 축이 {@code explanation_result} 가 아니라 {@code explanation_run}
 * 인 이유는, 결과가 아직 없는 런(PENDING·RUNNING·FAILED)도 운영자가 봐야 하는 대상이기
 * 때문이다(결과 축이면 실패한 분석이 화면에서 통째로 사라진다).
 */
public interface AnalysisRepository {

	/** 최신 분석부터. 원장 어휘(run_status·confidence_level·MIC)를 그대로 낸다 — UI 어휘 번역은 표시 층 소관. */
	List<AnalysisRow> list();

	/**
	 * @param summary         결과가 아직 없는 런이면 null — 상태별 안내 문구는 표시 층이 정한다
	 * @param confidenceLevel 결과가 없거나 원장이 판정을 비웠으면 null
	 * @param excluded         운영자 제외 오버레이(admin_activity_log 최신 액션 유도) — run_status 를
	 *                         덮지 않는다. 표시 층이 상태 배지만 EXCLUDED 로 바꾼다(ALPHA-602)
	 * @param corrected        운영자 정정 이력 존재 여부(같은 원장에서 유도)
	 * @param correctedSummary 최신 정정 본문(admin_activity_log details.after) — 정정 이력이 없으면
	 *                         null. 표시 층이 있으면 원장 원본 대신 이 문구를 낸다(원본은 불변)
	 */
	record AnalysisRow(String runId, String etfName, String ticker, String marketCode,
			double observedReturn, String runStatus, OffsetDateTime detectedAt,
			OffsetDateTime finishedAt, String summary, String confidenceLevel,
			boolean excluded, boolean corrected, String correctedSummary, List<EvidenceRow> evidence) {
	}

	/** 설명실행이 실제 사용한 문서 근거 한 건(뉴스·공시). */
	record EvidenceRow(String documentType, String title, String sourceCode,
			OffsetDateTime publishedAt) {
	}
}
