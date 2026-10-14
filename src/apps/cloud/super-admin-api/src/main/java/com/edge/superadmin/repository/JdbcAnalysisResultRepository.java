package com.edge.superadmin.repository;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.sql.Array;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

@Repository
public class JdbcAnalysisResultRepository implements AnalysisResultRepository {

	/** 목록에 싣는 제목·오류 메시지의 앞부분 길이(글자). 전체는 상세가 낸다. */
	private static final int TITLE_HEAD = 120;
	private static final int ERROR_HEAD = 200;

	/**
	 * 게시본 판정식 — v2 테넌트 조회({@code PublicationReader.latest})의 WHERE 와 같은 조건이다.
	 * 가격변동만 회수 조건이 더해진다({@code %s} 자리).
	 */
	private static final String PUBLISHED = """
			(a.status = 'completed' AND a.data_source = 'database' AND a.published_at IS NOT NULL %s)""";

	/**
	 * 목록 — 조회 한 번이다. 전체 행 수는 걸러진 행({@code f})에서 세고, 페이지는 같은 {@code f} 를
	 * 잘라 붙인다. 페이지가 범위를 넘어 비어도 전체 행 수는 나간다(그때는 행 열이 전부 null 인 한 줄).
	 *
	 * <p>날짜 범위는 {@code analysis_at} 에 직접 건다 — 색인 {@code (etf_code, analysis_at desc)} 를
	 * 그대로 탄다. 이름은 흐름 화면의 ETF 표와 같은 출처다({@code instrument} XKRX → {@code entity}).
	 * {@code (market_code, ticker)} 가 유일이라 행이 불어나지 않는다.
	 */
	private static final String LIST_SQL = """
			WITH f AS (
			    SELECT a.analysis_id, a.etf_code, a.analysis_at, a.status, a.data_source,
			           %1$s AS published, a.published_at, %2$s AS withdrawn_at,
			           left(%3$s, %4$d) AS title, left(a.error_message, %5$d) AS error_head
			      FROM %6$s a
			     WHERE a.analysis_at >= ? AND a.analysis_at < ?
			       AND (? OR a.data_source = 'database')
			       AND (?::text IS NULL OR a.etf_code = ?)
			       AND (?::text IS NULL OR a.status = ?)
			)
			SELECT c.total, p.*, COALESCE(e.display_name, p.etf_code) AS etf_name
			  FROM (SELECT count(*) AS total FROM f) c
			  LEFT JOIN LATERAL (SELECT * FROM f ORDER BY analysis_at DESC, analysis_id DESC
			                      LIMIT ? OFFSET ?) p ON true
			  LEFT JOIN instrument i ON i.market_code = 'XKRX' AND i.ticker = p.etf_code
			  LEFT JOIN entity e ON e.entity_id = i.instrument_id
			 ORDER BY p.analysis_at DESC, p.analysis_id DESC
			""";

	private static final String OUTLOOK_LIST_SQL = LIST_SQL.formatted(PUBLISHED.formatted(""),
			"NULL::timestamptz",
			"COALESCE(NULLIF(a.summary_title, ''), split_part(a.summary, E'\\n', 1))",
			TITLE_HEAD, ERROR_HEAD, "outlook_analyses");

	private static final String MOVEMENT_LIST_SQL = LIST_SQL.formatted(
			PUBLISHED.formatted("AND a.withdrawn_at IS NULL"), "a.withdrawn_at",
			"split_part(a.summary, E'\\n', 1)", TITLE_HEAD, ERROR_HEAD, "movement_analyses");

	private static final String HEAD_SQL = """
			SELECT a.*, %s AS published, COALESCE(e.display_name, a.etf_code) AS etf_name
			  FROM %s a
			  LEFT JOIN instrument i ON i.market_code = 'XKRX' AND i.ticker = a.etf_code
			  LEFT JOIN entity e ON e.entity_id = i.instrument_id
			 WHERE a.analysis_id = ?
			""";

	private static final String OUTLOOK_HEAD_SQL =
			HEAD_SQL.formatted(PUBLISHED.formatted(""), "outlook_analyses");

	private static final String MOVEMENT_HEAD_SQL =
			HEAD_SQL.formatted(PUBLISHED.formatted("AND a.withdrawn_at IS NULL"), "movement_analyses");

	/** section 은 detail → update 순(알파벳 순과 같다), 그 안은 저장된 position 순이다. */
	private static final String OUTLOOK_ITEMS_SQL = """
			SELECT item_id, section, change_type, position, title_keyword, bullets::text AS bullets,
			       sentence, sentiment, source_links::text AS source_links, source_as_of, tool_run_ids
			  FROM outlook_items WHERE analysis_id = ? ORDER BY section, position
			""";

	private static final String OUTLOOK_FACTORS_SQL = """
			SELECT type, sticker, sentence FROM outlook_factors WHERE analysis_id = ? ORDER BY type
			""";

	private static final String OUTLOOK_METRICS_SQL = """
			SELECT factor_type, metric_key, numeric_value, text_value, observed_date, observed_at, subject,
			       position, tool_run_ids
			  FROM outlook_factor_metrics WHERE analysis_id = ? ORDER BY factor_type, position
			""";

	private static final String OUTLOOK_ISSUES_SQL = """
			SELECT position, title_keyword, sentence, sentiment, tool_run_ids
			  FROM outlook_issue_items WHERE analysis_id = ? ORDER BY position
			""";

	private static final String OUTLOOK_KEYWORDS_SQL = """
			SELECT kind, position, label, tool_run_ids FROM outlook_conclusion_keywords
			 WHERE analysis_id = ? ORDER BY kind, position
			""";

	/** 고른 항목은 같은 날 앞선 분석의 것일 수 있다 — {@code analysis_id} 만 보면 빠진다. */
	private static final String MOVEMENT_ITEMS_SQL = """
			SELECT item_id, type, title_keyword, sentence, sentiment, source_as_of, tool_run_ids
			  FROM movement_items
			 WHERE analysis_id = ?
			    OR item_id = ANY(SELECT unnest(selected_item_ids) FROM movement_analyses WHERE analysis_id = ?)
			 ORDER BY created_at, item_id
			""";

	private final JdbcTemplate jdbc;
	private final ObjectMapper mapper;

	public JdbcAnalysisResultRepository(JdbcTemplate jdbc, ObjectMapper mapper) {
		this.jdbc = jdbc;
		this.mapper = mapper;
	}

	@Override
	@Transactional(readOnly = true)
	public Page list(ListQuery q) {
		boolean movement = q.kind() == Kind.MOVEMENT;
		long[] total = {0};
		List<ListRow> rows = new ArrayList<>();
		jdbc.query(movement ? MOVEMENT_LIST_SQL : OUTLOOK_LIST_SQL, rs -> {
			total[0] = rs.getLong("total");
			if (rs.getString("analysis_id") == null) {
				return;
			}
			OffsetDateTime withdrawnAt = rs.getObject("withdrawn_at", OffsetDateTime.class);
			rows.add(new ListRow(rs.getString("analysis_id"), rs.getString("etf_code"),
					rs.getString("etf_name"), rs.getObject("analysis_at", OffsetDateTime.class),
					rs.getString("status"), rs.getString("data_source"), rs.getBoolean("published"),
					rs.getObject("published_at", OffsetDateTime.class),
					movement ? Boolean.valueOf(withdrawnAt != null) : null, withdrawnAt,
					rs.getString("title"), rs.getString("error_head")));
		}, q.from(), q.to(), q.allSources(), q.etf(), q.etf(), q.status(), q.status(),
				q.size(), (long) q.page() * q.size());
		return new Page(total[0], q.page(), q.size(), List.copyOf(rows));
	}

	/**
	 * 상세 — 전망은 조회 6번(분석·근거 항목·요인·요인 지표·이슈 항목·결론 키워드), 가격변동은 2번
	 * (분석·항목)이다.
	 * 한 스냅샷에서 읽는다 — 분석 엔진이 그 사이에 항목을 써도 머리와 항목이 다른 시점이 되지 않는다.
	 */
	@Override
	@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
	public Detail detail(Kind kind, String analysisId) {
		boolean movement = kind == Kind.MOVEMENT;
		List<Detail> heads = jdbc.query(movement ? MOVEMENT_HEAD_SQL : OUTLOOK_HEAD_SQL,
				(rs, i) -> new Detail(rs.getString("analysis_id"), movement ? "movement" : "outlook",
						rs.getString("etf_code"), rs.getString("etf_name"),
						rs.getObject("analysis_at", OffsetDateTime.class), rs.getString("status"),
						rs.getString("data_source"), rs.getBoolean("published"),
						rs.getObject("published_at", OffsetDateTime.class),
						rs.getString("previous_analysis_id"), rs.getString("error_message"),
						movement ? null : outlookHead(rs), movement ? movementHead(rs) : null),
				analysisId);
		if (heads.isEmpty()) {
			return null;
		}
		Detail head = heads.get(0);
		if (movement) {
			Movement m = head.movement();
			List<MovementItem> items = jdbc.query(MOVEMENT_ITEMS_SQL, (rs, i) -> new MovementItem(
					rs.getString("item_id"), rs.getString("type"), rs.getString("title_keyword"),
					rs.getString("sentence"), rs.getString("sentiment"),
					rs.getObject("source_as_of", OffsetDateTime.class), strings(rs, "tool_run_ids")),
					analysisId, analysisId);
			return withMovement(head, new Movement(m.tradingDate(), m.summary(), m.selectedItemIds(),
					m.withdrawn(), m.withdrawnAt(), List.copyOf(items)));
		}
		Outlook o = head.outlook();
		List<OutlookItem> items = jdbc.query(OUTLOOK_ITEMS_SQL, (rs, i) -> new OutlookItem(
				rs.getString("item_id"), rs.getString("section"), rs.getString("change_type"),
				rs.getInt("position"), rs.getString("title_keyword"), json(rs, "bullets"),
				rs.getString("sentence"), rs.getString("sentiment"), json(rs, "source_links"),
				rs.getObject("source_as_of", OffsetDateTime.class), strings(rs, "tool_run_ids")),
				analysisId);
		List<OutlookFactor> factors = jdbc.query(OUTLOOK_FACTORS_SQL, (rs, i) -> new OutlookFactor(
				rs.getString("type"), rs.getString("sticker"), rs.getString("sentence")), analysisId);
		List<FactorMetric> metrics = jdbc.query(OUTLOOK_METRICS_SQL, (rs, i) -> new FactorMetric(
				rs.getString("factor_type"), rs.getString("metric_key"),
				rs.getBigDecimal("numeric_value"), rs.getString("text_value"),
				rs.getObject("observed_date", LocalDate.class),
				rs.getObject("observed_at", OffsetDateTime.class), rs.getString("subject"),
				rs.getInt("position"), strings(rs, "tool_run_ids")), analysisId);
		List<IssueItem> issues = jdbc.query(OUTLOOK_ISSUES_SQL, (rs, i) -> new IssueItem(
				rs.getInt("position"), rs.getString("title_keyword"), rs.getString("sentence"),
				rs.getString("sentiment"), strings(rs, "tool_run_ids")), analysisId);
		List<ConclusionKeyword> keywords = jdbc.query(OUTLOOK_KEYWORDS_SQL,
				(rs, i) -> new ConclusionKeyword(rs.getString("kind"), rs.getInt("position"),
						rs.getString("label"), strings(rs, "tool_run_ids")), analysisId);
		return withOutlook(head, new Outlook(o.sticker(), o.summaryTitle(), o.summary(),
				o.detailTitle(), o.detailMode(), o.conclusionTitle(), o.conclusionSentence(),
				o.changeCondition(), o.issueHeadline(), List.copyOf(items), List.copyOf(factors),
				List.copyOf(metrics), List.copyOf(issues), List.copyOf(keywords)));
	}

	private static Outlook outlookHead(ResultSet rs) throws SQLException {
		return new Outlook(rs.getString("outlook_sticker"), rs.getString("summary_title"),
				rs.getString("summary"), rs.getString("detail_title"), rs.getString("detail_mode"),
				rs.getString("conclusion_title"), rs.getString("conclusion_sentence"),
				rs.getString("change_condition"), rs.getString("issue_headline"), List.of(), List.of(),
				List.of(), List.of(), List.of());
	}

	private static Movement movementHead(ResultSet rs) throws SQLException {
		OffsetDateTime withdrawnAt = rs.getObject("withdrawn_at", OffsetDateTime.class);
		return new Movement(rs.getObject("trading_date", LocalDate.class), rs.getString("summary"),
				strings(rs, "selected_item_ids"), withdrawnAt != null, withdrawnAt, List.of());
	}

	private static Detail withOutlook(Detail d, Outlook outlook) {
		return new Detail(d.analysisId(), d.kind(), d.etfCode(), d.etfName(), d.analysisAt(),
				d.status(), d.dataSource(), d.published(), d.publishedAt(), d.previousAnalysisId(),
				d.errorMessage(), outlook, null);
	}

	private static Detail withMovement(Detail d, Movement movement) {
		return new Detail(d.analysisId(), d.kind(), d.etfCode(), d.etfName(), d.analysisAt(),
				d.status(), d.dataSource(), d.published(), d.publishedAt(), d.previousAnalysisId(),
				d.errorMessage(), null, movement);
	}

	private static List<String> strings(ResultSet rs, String column) throws SQLException {
		Array array = rs.getArray(column);
		return array == null ? List.of() : List.copyOf(Arrays.asList((String[]) array.getArray()));
	}

	private JsonNode json(ResultSet rs, String column) throws SQLException {
		String text = rs.getString(column);
		return text == null ? null : mapper.readTree(text);
	}
}
