package com.edge.superadmin.repository;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

/**
 * 뉴스 계보(Dataset Explorer) 읽기 전용 리포지토리 — "표시된 집계값을 목록으로 검증할 길"
 * (ALPHA-685, 멘토: "4천 건은 어디 있어").
 *
 * <p><b>이 화면이 답할 수 있는 범위는 RDS 가 아는 것까지다.</b> 문서 존재(1단계)와 분석 사용
 * (5단계)은 canonical 테이블로 답한다. 중복 제거·종목 연결·추출 terminal(NO_EVENT/INVALID/
 * FAILED 구분)은 <b>RDS 에 판정 데이터가 없다</b> — S3 quality log·feature parquet 에만 있다.
 * 그래서 추출 축은 "assertion 존재"로만 근사하고, 그 이상을 주장하지 않는다(원장 승격은 후속).
 *
 * <p>{@code run_id} 는 어느 문서 테이블에도 없다 — 런 단위 계보는 불가하고, 날짜(수집 시각
 * {@code available_at} 의 KST 날짜) 단위로 자른다.
 */
public interface NewsLineageRepository {

	/** @param dateKst null 이면 전체 누적 */
	LineageSummary summary(LocalDate dateKst);

	List<LineageDocument> documents(LocalDate dateKst, int limit);

	/**
	 * 집계와 근거 목록을 <b>한 스냅샷</b>에서 — 따로 부르면 두 조회 사이에 writer 가 커밋해
	 * "집계 0 인데 목록엔 문서" 같은, 어느 시점에도 존재하지 않은 조합이 화면에 조립된다
	 * (드릴다운 네 조회를 REPEATABLE READ 로 묶는 것과 같은 이유). 화면 경로는 이것만 쓴다.
	 */
	Lineage lineage(LocalDate dateKst, int limit);

	record Lineage(LineageSummary summary, List<LineageDocument> documents) {
	}

	/**
	 * 단계별 문서 수 — 단위는 전부 <b>문서(기사)</b>다. {@code withAssertion} 은 "추출 성공"이
	 * 아니라 "구조화 증거가 남은 문서"다(없음 = NO_EVENT·실패·미실행이 한 통 — 구분 미계측).
	 */
	record LineageSummary(long totalDocuments, long documentsWithAssertion,
			long documentsUsedInAnalysis) {
	}

	/** 문서 한 건의 계보 축약 — 존재 → 증거(assertion) → 분석 사용. */
	record LineageDocument(String documentId, String title, String sourceCode,
			OffsetDateTime publishedAt, OffsetDateTime availableAt,
			long assertionCount, boolean usedInAnalysis) {
	}
}
