package com.edge.superadmin.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

/**
 * {@link NewsLineageRepository} 의 JdbcTemplate 구현(ALPHA-685).
 *
 * <p>날짜 필터는 <b>수집 시각({@code available_at})의 KST 날짜</b>다 — 게시 시각은 소스가
 * 과거 기사를 늦게 줄 수 있어 "그날 파이프라인이 다룬 문서"와 어긋난다. 표현식 필터라
 * 풀스캔이지만 문서 30만 규모의 콘솔 단발 조회라 감당 범위다(느려지면 함수 인덱스 후속).
 */
@Repository
public class JdbcNewsLineageRepository implements NewsLineageRepository {

	private static final String KST_DATE = "(d.available_at AT TIME ZONE 'Asia/Seoul')::date";

	/**
	 * 세 카운트를 한 문장으로 — 분리하면 조회 사이에 writer 가 끼어 "with > total" 같은
	 * 존재한 적 없는 조합이 화면에 조립된다(드릴다운 네 조회를 한 스냅샷으로 묶는 것과 같은 이유).
	 */
	private static final String SUMMARY_SQL = """
			SELECT count(*) AS total_documents,
			       count(*) FILTER (WHERE EXISTS (
			           SELECT 1 FROM document_assertion a
			            WHERE a.document_id = d.document_id)) AS documents_with_assertion,
			       count(*) FILTER (WHERE EXISTS (
			           SELECT 1 FROM document_assertion a
			             JOIN event_evidence ev ON ev.assertion_id = a.assertion_id
			             JOIN explanation_run_event_evidence ree ON ree.evidence_id = ev.evidence_id
			            WHERE a.document_id = d.document_id)) AS documents_used_in_analysis
			  FROM document d
			 WHERE d.document_type = 'NEWS'
			""";

	private static final String DOCUMENTS_SQL = """
			SELECT d.document_id, d.title, d.source_code, d.published_at, d.available_at,
			       (SELECT count(*) FROM document_assertion a
			         WHERE a.document_id = d.document_id) AS assertion_count,
			       EXISTS (
			           SELECT 1 FROM document_assertion a
			             JOIN event_evidence ev ON ev.assertion_id = a.assertion_id
			             JOIN explanation_run_event_evidence ree ON ree.evidence_id = ev.evidence_id
			            WHERE a.document_id = d.document_id) AS used_in_analysis
			  FROM document d
			 WHERE d.document_type = 'NEWS'
			""";

	/** 수집 시각 내림차순 — "방금 들어온 것부터". 동률 해소는 격자와 같은 이유(id). */
	private static final String DOCUMENTS_TAIL = """
			 ORDER BY d.available_at DESC, d.document_id DESC
			 LIMIT ?
			""";

	private final JdbcTemplate jdbc;

	public JdbcNewsLineageRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	@Override
	@Transactional(readOnly = true,
			isolation = org.springframework.transaction.annotation.Isolation.REPEATABLE_READ)
	public Lineage lineage(LocalDate dateKst, int limit) {
		// 두 조회가 이 트랜잭션에 참여해 한 스냅샷을 읽는다(인터페이스 주석 참조).
		return new Lineage(summary(dateKst), documents(dateKst, limit));
	}

	@Override
	@Transactional(readOnly = true)
	public LineageSummary summary(LocalDate dateKst) {
		String sql = dateKst == null ? SUMMARY_SQL
				: SUMMARY_SQL + "   AND " + KST_DATE + " = ?";
		Object[] args = dateKst == null ? new Object[]{} : new Object[]{dateKst};
		return jdbc.query(sql, JdbcNewsLineageRepository::mapSummary, args).get(0);
	}

	@Override
	@Transactional(readOnly = true)
	public List<LineageDocument> documents(LocalDate dateKst, int limit) {
		if (dateKst == null) {
			return jdbc.query(DOCUMENTS_SQL + DOCUMENTS_TAIL,
					JdbcNewsLineageRepository::mapDocument, limit);
		}
		return jdbc.query(DOCUMENTS_SQL + "   AND " + KST_DATE + " = ?" + DOCUMENTS_TAIL,
				JdbcNewsLineageRepository::mapDocument, dateKst, limit);
	}

	private static LineageSummary mapSummary(ResultSet rs, int rowNum) throws SQLException {
		return new LineageSummary(
				rs.getLong("total_documents"),
				rs.getLong("documents_with_assertion"),
				rs.getLong("documents_used_in_analysis"));
	}

	private static LineageDocument mapDocument(ResultSet rs, int rowNum) throws SQLException {
		return new LineageDocument(
				rs.getString("document_id"),
				rs.getString("title"),
				rs.getString("source_code"),
				rs.getObject("published_at", OffsetDateTime.class),
				rs.getObject("available_at", OffsetDateTime.class),
				rs.getLong("assertion_count"),
				rs.getBoolean("used_in_analysis"));
	}
}
