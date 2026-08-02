package com.edge.superadmin.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.List;

/**
 * {@link NewsLineageRepository} 의 JdbcTemplate 구현(ALPHA-685·697).
 *
 * <p>날짜 필터는 <b>수집 시각({@code available_at})의 KST 날짜</b>다 — 게시 시각은 소스가
 * 과거 기사를 늦게 줄 수 있어 "그날 파이프라인이 다룬 문서"와 어긋난다. 표현식 필터라
 * 풀스캔이지만 문서 30만 규모의 콘솔 단발 조회라 감당 범위다(느려지면 함수 인덱스 후속).
 *
 * <p>추출 요약의 날짜 축은 {@code news_extraction_job.created_at} 의 KST <b>반개구간</b>이다
 * — /minute 콘솔과 같은 규칙이고, {@code idx_news_job_created_at} 를 그대로 탄다.
 */
@Repository
public class JdbcNewsLineageRepository implements NewsLineageRepository {

	private static final String KST_DATE = "(d.available_at AT TIME ZONE 'Asia/Seoul')::date";
	private static final ZoneId KST = ZoneId.of("Asia/Seoul");

	/**
	 * 단계 정의 SQL 조각 — 집계(FILTER)와 목록 필터(WHERE)가 <b>같은 조각</b>을 쓴다.
	 * 갈리면 타일 숫자와 클릭 결과 목록이 서로 다른 집합을 말한다(ALPHA-697 드릴다운 계약).
	 */
	private static final String HAS_ASSERTION = """
			EXISTS (SELECT 1 FROM document_assertion a
			         WHERE a.document_id = d.document_id)""";

	private static final String USED_IN_ANALYSIS = """
			EXISTS (SELECT 1 FROM document_assertion a
			          JOIN event_evidence ev ON ev.assertion_id = a.assertion_id
			          JOIN explanation_run_event_evidence ree ON ree.evidence_id = ev.evidence_id
			         WHERE a.document_id = d.document_id)""";

	/**
	 * 세 카운트를 한 문장으로 — 분리하면 조회 사이에 writer 가 끼어 "with > total" 같은
	 * 존재한 적 없는 조합이 화면에 조립된다(드릴다운 네 조회를 한 스냅샷으로 묶는 것과 같은 이유).
	 */
	private static final String SUMMARY_SQL = """
			SELECT count(*) AS total_documents,
			       count(*) FILTER (WHERE %s) AS documents_with_assertion,
			       count(*) FILTER (WHERE %s) AS documents_used_in_analysis
			  FROM document d
			 WHERE d.document_type = 'NEWS'
			""".formatted(HAS_ASSERTION, USED_IN_ANALYSIS);

	private static final String DOCUMENTS_SQL = """
			SELECT d.document_id, d.title, d.source_code, d.source_uri, nd.publisher,
			       d.published_at, d.available_at,
			       (SELECT count(*) FROM document_assertion a
			         WHERE a.document_id = d.document_id) AS assertion_count,
			       %s AS used_in_analysis
			  FROM document d
			  LEFT JOIN news_document nd ON nd.document_id = d.document_id
			 WHERE d.document_type = 'NEWS'
			""".formatted(USED_IN_ANALYSIS);

	/** 수집 시각 내림차순 — "방금 들어온 것부터". 동률 해소는 격자와 같은 이유(id). */
	private static final String DOCUMENTS_TAIL = """
			 ORDER BY d.available_at DESC, d.document_id DESC
			 LIMIT ?
			""";

	private static final String EXTRACTION_COUNTS_SQL = """
			SELECT count(*) FILTER (WHERE status = 'SUCCEEDED') AS succeeded,
			       count(*) FILTER (WHERE status = 'DEAD') AS dead
			  FROM news_extraction_job
			""";

	/** NULLS LAST — 사유 미기록(NULL)도 한 행으로 내리되 이름 있는 사유 뒤에 둔다. */
	private static final String EXTRACTION_DEAD_BY_CODE_SQL = """
			SELECT error_code, count(*) AS cnt
			  FROM news_extraction_job
			 WHERE status = 'DEAD'
			""";
	private static final String EXTRACTION_DEAD_BY_CODE_TAIL = """
			 GROUP BY error_code
			 ORDER BY cnt DESC, error_code ASC NULLS LAST
			""";

	private final JdbcTemplate jdbc;

	public JdbcNewsLineageRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	@Override
	@Transactional(readOnly = true,
			isolation = org.springframework.transaction.annotation.Isolation.REPEATABLE_READ)
	public Lineage lineage(LocalDate dateKst, Stage stage, int limit) {
		// 세 조회가 이 트랜잭션에 참여해 한 스냅샷을 읽는다(인터페이스 주석 참조).
		return new Lineage(summary(dateKst), documents(dateKst, stage, limit),
				extraction(dateKst));
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
	public List<LineageDocument> documents(LocalDate dateKst, Stage stage, int limit) {
		StringBuilder sql = new StringBuilder(DOCUMENTS_SQL);
		List<Object> args = new ArrayList<>();
		if (dateKst != null) {
			sql.append("   AND ").append(KST_DATE).append(" = ?\n");
			args.add(dateKst);
		}
		if (stage != null) {
			sql.append("   AND ").append(switch (stage) {
				case STRUCTURED -> HAS_ASSERTION;
				case UNSTRUCTURED -> "NOT " + HAS_ASSERTION;
				case USED -> USED_IN_ANALYSIS;
			}).append("\n");
		}
		sql.append(DOCUMENTS_TAIL);
		args.add(limit);
		return jdbc.query(sql.toString(), JdbcNewsLineageRepository::mapDocument,
				args.toArray());
	}

	@Override
	@Transactional(readOnly = true)
	public ExtractionSummary extraction(LocalDate dateKst) {
		String countsSql = EXTRACTION_COUNTS_SQL;
		String byCodeSql = EXTRACTION_DEAD_BY_CODE_SQL;
		Object[] countsArgs = new Object[]{};
		Object[] byCodeArgs = new Object[]{};
		if (dateKst != null) {
			OffsetDateTime dayStart = dateKst.atStartOfDay(KST).toOffsetDateTime();
			OffsetDateTime dayEnd = dateKst.plusDays(1).atStartOfDay(KST).toOffsetDateTime();
			countsSql += " WHERE created_at >= ? AND created_at < ?";
			byCodeSql += "   AND created_at >= ? AND created_at < ?\n";
			countsArgs = new Object[]{dayStart, dayEnd};
			byCodeArgs = new Object[]{dayStart, dayEnd};
		}
		ExtractionSummary counts = jdbc.query(countsSql,
				(rs, i) -> new ExtractionSummary(rs.getLong("succeeded"), rs.getLong("dead"),
						List.of()), countsArgs).get(0);
		List<ErrorCodeCount> byCode = jdbc.query(byCodeSql + EXTRACTION_DEAD_BY_CODE_TAIL,
				(rs, i) -> new ErrorCodeCount(rs.getString("error_code"), rs.getLong("cnt")),
				byCodeArgs);
		return new ExtractionSummary(counts.succeeded(), counts.dead(), byCode);
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
				rs.getString("publisher"),
				rs.getString("source_uri"),
				rs.getObject("published_at", OffsetDateTime.class),
				rs.getObject("available_at", OffsetDateTime.class),
				rs.getLong("assertion_count"),
				rs.getBoolean("used_in_analysis"));
	}
}
