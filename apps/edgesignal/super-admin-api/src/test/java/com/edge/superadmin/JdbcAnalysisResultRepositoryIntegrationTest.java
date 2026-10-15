package com.edge.superadmin;

import com.edge.superadmin.repository.AnalysisResultRepository;
import com.edge.superadmin.repository.AnalysisResultRepository.Detail;
import com.edge.superadmin.repository.AnalysisResultRepository.Kind;
import com.edge.superadmin.repository.AnalysisResultRepository.ListQuery;
import com.edge.superadmin.repository.AnalysisResultRepository.ListRow;
import com.edge.superadmin.repository.AnalysisResultRepository.Page;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.tuple;

/**
 * 새 분석 엔진 결과 목록·상세(ALPHA-1333)를 실 cloud 스키마로 검증한다.
 *
 * <p>지키는 것 셋 — ① 목록이 기본으로 운영 산출({@code data_source='database'})만 보인다(합성 입력이
 * 섞이면 운영자가 테스트 실행을 고객에게 나간 분석으로 읽는다) ② "게시됨"이 <b>테넌트가 실제로 받는
 * 것</b>의 조건과 같다(갈리면 콘솔은 게시됐다는데 테넌트는 못 받는다 — 테넌트의 최신 조회는 이 조건을
 * 만족하는 행 가운데 ETF 마다 최근 한 건을 고른다) ③ 상세가 저장된 항목을 빠짐없이 낸다.
 */
@Transactional
class JdbcAnalysisResultRepositoryIntegrationTest extends CloudPostgresIntegrationTest {

	private static final ZoneId KST = ZoneId.of("Asia/Seoul");
	private static final LocalDate DAY = LocalDate.parse("2026-10-13");

	/**
	 * analysis-engine-v2 {@code api/publications.py} {@code PublicationReader.latest} 의 WHERE 를
	 * <b>옮겨 적은 것</b>이다. 테넌트가 받는 게시본이 이 조건이라, 목록의 published 가 이 조건과
	 * 행 단위로 같아야 한다. 옮겨 적은 조건이 저쪽 원문과 같은지는 아래
	 * {@code 옮겨_적은_조건이_엔진_원문에_있다} 가 원문 파일을 읽어 잰다 — 저쪽이 바뀌면 그 테스트가 먼저 깨진다.
	 */
	private static final String TENANT_OUTLOOK = """
			SELECT analysis_id FROM outlook_analyses WHERE status='completed'
			   AND data_source='database' AND published_at IS NOT NULL""";
	private static final String TENANT_MOVEMENT = """
			SELECT analysis_id FROM movement_analyses WHERE status='completed'
			   AND data_source='database' AND published_at IS NOT NULL AND withdrawn_at IS NULL""";

	@Autowired
	private AnalysisResultRepository repository;

	@Autowired
	private JdbcTemplate jdbc;

	private void outlook(String id, String etf, String at, String status, String source,
			String publishedAt, String error) {
		jdbc.update("""
				INSERT INTO outlook_analyses (analysis_id, etf_code, analysis_at, status, data_source,
				       published_at, error_message, summary_title, summary)
				VALUES (?,?,?::timestamptz,?,?,?::timestamptz,?,?,?)
				""", id, etf, at, status, source, publishedAt, error, null, "요약 첫 줄\n둘째 줄");
	}

	private void movement(String id, String etf, String at, String status, String source,
			String publishedAt, String withdrawnAt) {
		jdbc.update("""
				INSERT INTO movement_analyses (analysis_id, etf_code, analysis_at, status, data_source,
				       published_at, withdrawn_at, trading_date, summary)
				VALUES (?,?,?::timestamptz,?,?,?::timestamptz,?::timestamptz,
				        (?::timestamptz AT TIME ZONE 'Asia/Seoul')::date, ?)
				""", id, etf, at, status, source, publishedAt, withdrawnAt, at, "움직임 요약");
	}

	private Page list(Kind kind, String etf, String status, boolean allSources, int page, int size) {
		return repository.list(new ListQuery(kind, DAY.atStartOfDay(KST).toOffsetDateTime(),
				DAY.plusDays(1).atStartOfDay(KST).toOffsetDateTime(), etf, status, allSources, page, size));
	}

	private static List<String> ids(Page page) {
		return page.rows().stream().map(ListRow::analysisId).toList();
	}

	@Test
	void 옮겨_적은_조건이_엔진_원문에_있다() throws Exception {
		Path source = Path.of(System.getProperty("cloud.migrations.dir"))
				.resolve("../../../apps/engine/analysis-engine-v2/src/edge_analysis_v2/api/publications.py")
				.normalize();
		String text = Files.readString(source);
		assertThat(text)
				.contains("AND status='completed' AND data_source='database' AND published_at IS NOT NULL")
				.contains("active_filter=sql.SQL('AND withdrawn_at IS NULL') if kind=='movement' else sql.SQL('')");
	}

	@Test
	void 목록은_기본으로_운영_산출만_싣고_전체_출처는_켤_때만_싣는다() {
		outlook("db-1", "069500", "2026-10-13T09:00:00+09:00", "completed", "database",
				"2026-10-13T09:05:00+09:00", null);
		outlook("syn-1", "069500", "2026-10-13T10:00:00+09:00", "completed", "synthetic",
				"2026-10-13T10:05:00+09:00", null);
		outlook("unk-1", "069500", "2026-10-13T11:00:00+09:00", "completed", "unknown", null, null);
		/* 00:05 KST 는 UTC 로 전날이다 — 범위를 KST 로 안 잡으면 빠진다 */
		outlook("db-0005", "069500", "2026-10-13T00:05:00+09:00", "running", "database", null, null);
		/* 전날 23:59 KST 는 조회일이 아니다 */
		outlook("db-prev", "069500", "2026-10-12T23:59:00+09:00", "completed", "database", null, null);

		assertThat(ids(list(Kind.OUTLOOK, null, null, false, 0, 50)))
				.as("기본은 database 만 — 최신순")
				.containsExactly("db-1", "db-0005");
		assertThat(ids(list(Kind.OUTLOOK, null, null, true, 0, 50)))
				.as("전체 출처를 켜면 합성·출처 미상도 함께")
				.containsExactly("unk-1", "syn-1", "db-1", "db-0005");
		assertThat(list(Kind.OUTLOOK, null, null, true, 0, 50).rows())
				.extracting(ListRow::dataSource)
				.containsExactly("unknown", "synthetic", "database", "database");
		/* 종류 탭은 표 하나만 본다 — 전망 행이 가격변동 목록에 새지 않는다 */
		assertThat(list(Kind.MOVEMENT, null, null, true, 0, 50).rows()).isEmpty();
	}

	@Test
	void ETF와_상태로_거르고_페이지가_범위를_넘어도_전체_수를_낸다() {
		outlook("a-ok", "069500", "2026-10-13T09:00:00+09:00", "completed", "database", null, null);
		outlook("a-fail", "069500", "2026-10-13T10:00:00+09:00", "failed", "database", null,
				"x".repeat(500));
		outlook("b-ok", "102110", "2026-10-13T11:00:00+09:00", "completed", "database", null, null);

		assertThat(ids(list(Kind.OUTLOOK, "069500", null, false, 0, 50))).containsExactly("a-fail", "a-ok");
		assertThat(ids(list(Kind.OUTLOOK, null, "failed", false, 0, 50))).containsExactly("a-fail");
		assertThat(ids(list(Kind.OUTLOOK, "102110", "failed", false, 0, 50))).isEmpty();

		Page first = list(Kind.OUTLOOK, null, null, false, 0, 2);
		Page second = list(Kind.OUTLOOK, null, null, false, 1, 2);
		Page beyond = list(Kind.OUTLOOK, null, null, false, 5, 2);
		assertThat(ids(first)).containsExactly("b-ok", "a-fail");
		assertThat(ids(second)).containsExactly("a-ok");
		assertThat(beyond.rows()).isEmpty();
		/* 빈 페이지에서도 전체 수가 0 으로 무너지지 않는다 — 0 이면 화면이 "분석 없음"을 말한다 */
		assertThat(List.of(first.total(), second.total(), beyond.total())).containsOnly(3L);

		/* 이름은 XKRX 종목에서만 — 같은 티커가 다른 시장에 있어도 그 이름을 쓰지 않는다 */
		jdbc.update("INSERT INTO entity (entity_id, entity_type, display_name) VALUES ('i-krx', 'INSTRUMENT', 'KODEX 200'), ('i-us', 'INSTRUMENT', '다른 시장')");
		jdbc.update("INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type) VALUES ('i-krx', 'XKRX', '069500', 'ETF'), ('i-us', 'XNAS', '069500', 'ETF')");
		assertThat(list(Kind.OUTLOOK, null, null, false, 0, 50).rows())
				.extracting(ListRow::analysisId, ListRow::etfName)
				.containsExactly(tuple("b-ok", "102110"), tuple("a-fail", "KODEX 200"), tuple("a-ok", "KODEX 200"));

		ListRow failed = list(Kind.OUTLOOK, null, "failed", false, 0, 50).rows().get(0);
		assertThat(failed.errorHead()).hasSize(200);
		assertThat(failed.title()).as("요약 제목이 없으면 요약 첫 줄").isEqualTo("요약 첫 줄");
		assertThat(failed.withdrawn()).as("전망에는 회수 열이 없다 — 거짓이 아니라 값 없음").isNull();
	}

	@Test
	void 게시됨은_테넌트_조회가_게시본으로_내는_행과_같다() {
		String at = "2026-10-13T09:00:00+09:00";
		String pub = "2026-10-13T09:05:00+09:00";
		movement("m-pub", "069500", at, "completed", "database", pub, null);
		movement("m-withdrawn", "069500", at, "completed", "database", pub, "2026-10-13T12:00:00+09:00");
		movement("m-failed-withdrawn", "069500", at, "failed", "database", null, "2026-10-13T12:00:00+09:00");
		movement("m-syn", "069500", at, "completed", "synthetic", pub, null);
		movement("m-unpub", "069500", at, "completed", "database", null, null);
		movement("m-running", "069500", at, "running", "database", null, null);
		/* 게시 시각이 남은 실패 행 — status 조건이 빠지면 이 행이 게시로 보인다 */
		movement("m-failed-pub", "069500", at, "failed", "database", pub, null);
		outlook("o-failed-pub", "069500", at, "failed", "database", pub, null);
		outlook("o-pub", "069500", at, "completed", "database", pub, null);
		outlook("o-syn", "069500", at, "completed", "synthetic", pub, null);
		outlook("o-unpub", "069500", at, "completed", "database", null, null);

		List<ListRow> movements = list(Kind.MOVEMENT, null, null, true, 0, 50).rows();
		List<ListRow> outlooks = list(Kind.OUTLOOK, null, null, true, 0, 50).rows();
		assertThat(published(movements)).isEqualTo(new HashSet<>(jdbc.queryForList(TENANT_MOVEMENT, String.class)))
				.containsExactly("m-pub");
		assertThat(published(outlooks)).isEqualTo(new HashSet<>(jdbc.queryForList(TENANT_OUTLOOK, String.class)))
				.containsExactly("o-pub");

		/* 회수는 게시 여부와 따로 보인다 — 회수된 게시본은 published=false · withdrawn=true 다 */
		ListRow withdrawn = movements.stream().filter(r -> r.analysisId().equals("m-withdrawn")).findFirst().orElseThrow();
		assertThat(withdrawn.withdrawn()).isTrue();
		assertThat(withdrawn.publishedAt()).isNotNull();
		assertThat(movements.stream().filter(r -> r.analysisId().equals("m-pub")).findFirst().orElseThrow()
				.withdrawn()).isFalse();
		/* 상세도 같은 판정식이다 */
		assertThat(repository.detail(Kind.MOVEMENT, "m-withdrawn").published()).isFalse();
		assertThat(repository.detail(Kind.MOVEMENT, "m-withdrawn").movement().withdrawn()).isTrue();
		assertThat(repository.detail(Kind.MOVEMENT, "m-pub").published()).isTrue();
		assertThat(repository.detail(Kind.OUTLOOK, "o-syn").published()).isFalse();
	}

	private static Set<String> published(List<ListRow> rows) {
		return rows.stream().filter(ListRow::published).map(ListRow::analysisId).collect(Collectors.toSet());
	}

	/**
	 * 전망 한 건의 하위 행 — 정렬 열의 <b>역순으로</b> 넣는다. 넣은 순서가 기대 순서와 같으면 ORDER BY 를
	 * 지워도 테스트가 통과한다(물리 순서가 우연히 맞는다). 근거 항목의 item_id 도 position 과 거꾸로 둔다 —
	 * {@code (analysis_id, section, item_id)} 유일 색인 순서로 읽혀도 우연히 맞지 않게.
	 */
	private void outlookContent(String id, int items) {
		jdbc.update("""
				INSERT INTO outlook_items (row_id, analysis_id, item_id, section, change_type, position,
				       title_keyword, sentence, sentiment)
				VALUES (?, ?, 'item-0', 'update', 'modified', 0, '키워드', '바뀐 문장', 'negative')
				""", id + "-u0", id);
		for (int i = items - 1; i >= 0; i--) {
			jdbc.update("""
					INSERT INTO outlook_items (row_id, analysis_id, item_id, section, position, title_keyword,
					       bullets, source_links, tool_run_ids)
					VALUES (?, ?, ?, 'detail', ?, '키워드', '["a","b"]'::jsonb,
					        '[{"url":"https://x"}]'::jsonb, ARRAY['run-1'])
					""", id + "-d" + i, id, "item-" + (items - i), i);
		}
		for (String type : List.of("수급", "차트", "이슈")) {
			jdbc.update("""
					INSERT INTO outlook_factors (row_id, analysis_id, type, sticker, sentence)
					VALUES (?, ?, ?, '중립', '요인 문장')""", id + "-f-" + type, id, type);
		}
		for (int i = 1; i >= 0; i--) {
			jdbc.update("""
					INSERT INTO outlook_factor_metrics (analysis_id, factor_type, metric_key, numeric_value,
					       observed_date, position, tool_run_ids)
					VALUES (?, '차트', ?, 9007199254740993.123456789, DATE '2026-10-13', ?, ARRAY['run-m'])
					""", id, "metric-" + i, i);
			jdbc.update("""
					INSERT INTO outlook_issue_items (analysis_id, position, title_keyword, sentence, sentiment,
					       tool_run_ids)
					VALUES (?, ?, '이슈 키워드', '이슈 문장', 'neutral', ARRAY['run-i'])
					""", id, i);
		}
		jdbc.update("""
				INSERT INTO outlook_conclusion_keywords (row_id, analysis_id, kind, position, label)
				VALUES (?, ?, 'support', 0, '지지'), (?, ?, 'burden', 0, '부담')""",
				id + "-k0", id, id + "-k1", id);
	}

	@Test
	void 전망_상세는_엔진이_저장한_하위_행을_빠짐없이_엔진_순서로_낸다() {
		outlook("o-1", "069500", "2026-10-13T09:00:00+09:00", "completed", "database",
				"2026-10-13T09:05:00+09:00", null);
		jdbc.update("""
				UPDATE outlook_analyses SET outlook_sticker='상승', summary_title='제목', detail_title='상세',
				       detail_mode='create', conclusion_title='결론', conclusion_sentence='문장',
				       change_condition='조건', issue_headline='이슈 헤드라인' WHERE analysis_id='o-1'""");
		outlookContent("o-1", 3);
		/* 다른 분석의 행이 섞이지 않는다 — 표마다 하위 행을 둔다 */
		outlook("o-2", "069500", "2026-10-13T10:00:00+09:00", "completed", "database", null, null);
		outlookContent("o-2", 1);

		/* 색인 스캔을 끈다 — (analysis_id, section, position) 유일 색인으로 읽히면 ORDER BY 를 지워도
		 * 순서가 우연히 맞아 정렬을 재지 못한다(변이로 확인했다). 이 테스트 트랜잭션에만 걸린다. */
		jdbc.execute("SET LOCAL enable_indexscan = off");
		jdbc.execute("SET LOCAL enable_bitmapscan = off");
		Detail d = repository.detail(Kind.OUTLOOK, "o-1");
		assertThat(d.movement()).isNull();
		assertThat(d.outlook().items()).hasSize(stored("outlook_items", "o-1"));
		assertThat(d.outlook().factors()).hasSize(stored("outlook_factors", "o-1"));
		assertThat(d.outlook().factorMetrics()).hasSize(stored("outlook_factor_metrics", "o-1"));
		assertThat(d.outlook().issueItems()).hasSize(stored("outlook_issue_items", "o-1"));
		assertThat(d.outlook().conclusionKeywords()).hasSize(stored("outlook_conclusion_keywords", "o-1"));

		/* 엔진 확인용 조회(storage/inspection.py)와 같은 순서 */
		assertThat(d.outlook().items()).extracting(i -> i.section() + i.position())
				.containsExactly("detail0", "detail1", "detail2", "update0");
		List<String> factorOrder = jdbc.queryForList(
				"SELECT type FROM outlook_factors WHERE analysis_id = 'o-1' ORDER BY type", String.class);
		assertThat(factorOrder).as("넣은 순서와 달라야 정렬을 잰다").isNotEqualTo(List.of("수급", "차트", "이슈"));
		assertThat(d.outlook().factors()).extracting(f -> f.type()).isEqualTo(factorOrder);
		assertThat(d.outlook().factorMetrics()).extracting(m -> m.position()).containsExactly(0, 1);
		assertThat(d.outlook().issueItems()).extracting(i -> i.position()).containsExactly(0, 1);
		assertThat(d.outlook().conclusionKeywords()).extracting(k -> k.kind()).containsExactly("burden", "support");

		/* 저장된 값을 그대로 낸다 */
		assertThat(d.outlook().issueHeadline()).isEqualTo("이슈 헤드라인");
		assertThat(d.outlook().factorMetrics().get(0).numericValue()).isEqualByComparingTo("9007199254740993.123456789");
		assertThat(d.outlook().factorMetrics().get(0).observedDate()).isEqualTo(DAY);
		assertThat(d.outlook().items().get(0).bullets().toString()).isEqualTo("[\"a\",\"b\"]");
		assertThat(d.outlook().items().get(0).sourceLinks().toString()).isEqualTo("[{\"url\":\"https://x\"}]");
		assertThat(d.outlook().items().get(0).toolRunIds()).containsExactly("run-1");
		assertThat(d.outlook().sticker()).isEqualTo("상승");
		assertThat(repository.detail(Kind.OUTLOOK, "없는-id")).isNull();
		/* 종류를 틀리면 다른 표에서 찾지 않는다 */
		assertThat(repository.detail(Kind.MOVEMENT, "o-1")).isNull();
	}

	private void movementItem(String id, String analysisId, int second) {
		jdbc.update("""
				INSERT INTO movement_items (item_id, analysis_id, type, title_keyword, sentence, sentiment,
				       tool_run_ids, created_at)
				VALUES (?, ?, '이슈', '키워드', '근거 문장', 'positive', ARRAY['r'],
				        '2026-10-13T09:00:00+09:00'::timestamptz + (? * interval '1 second'))
				""", id, analysisId, second);
	}

	/**
	 * 엔진은 같은 날 앞선 분석이 만든 항목을 다시 고를 수 있다({@code storage/publications.py} — 고른 id 가
	 * 이번에 만든 것이 아니면 그대로 쓴다). 그 항목은 앞선 분석의 {@code analysis_id} 로 저장돼 있어,
	 * {@code analysis_id} 만으로 읽으면 테넌트에 나간 고른 항목이 상세에서 빠진다.
	 */
	@Test
	void 가격변동_상세는_만든_항목_전부와_앞선_분석에서_고른_항목을_내고_실패면_오류_전체를_낸다() {
		movement("m-0", "069500", "2026-10-13T08:00:00+09:00", "completed", "database", null, null);
		movementItem("old-picked", "m-0", -60);
		movementItem("old-unpicked", "m-0", -59);
		movement("m-1", "069500", "2026-10-13T09:00:00+09:00", "completed", "database",
				"2026-10-13T09:05:00+09:00", null);
		for (int i = 3; i >= 0; i--) {
			movementItem("mi-" + i, "m-1", i);
		}
		jdbc.update("""
				UPDATE movement_analyses SET selected_item_ids = ARRAY['mi-2','old-picked'],
				       previous_analysis_id = 'm-0' WHERE analysis_id = 'm-1'""");
		String error = "오류 ".repeat(300);
		movement("m-fail", "069500", "2026-10-13T10:00:00+09:00", "failed", "database", null, null);
		jdbc.update("UPDATE movement_analyses SET error_message=? WHERE analysis_id='m-fail'", error);

		Detail d = repository.detail(Kind.MOVEMENT, "m-1");
		assertThat(d.outlook()).isNull();
		Integer stored = jdbc.queryForObject("""
				SELECT count(*) FROM movement_items WHERE analysis_id = 'm-1'
				    OR item_id = ANY(SELECT unnest(selected_item_ids) FROM movement_analyses WHERE analysis_id = 'm-1')
				""", Integer.class);
		assertThat(d.movement().items()).hasSize(stored).hasSize(5);
		assertThat(d.movement().items()).extracting(i -> i.itemId())
				.as("고른 앞선 항목은 들고 고르지 않은 앞선 항목은 빠진다 — 만든 시각 순")
				.containsExactly("old-picked", "mi-0", "mi-1", "mi-2", "mi-3");
		assertThat(d.movement().selectedItemIds()).as("저장된 순서 그대로").containsExactly("mi-2", "old-picked");
		assertThat(d.previousAnalysisId()).isEqualTo("m-0");
		assertThat(d.movement().tradingDate()).isEqualTo(DAY);
		assertThat(d.analysisAt()).isEqualTo(OffsetDateTime.parse("2026-10-13T09:00:00+09:00"));

		Detail failed = repository.detail(Kind.MOVEMENT, "m-fail");
		assertThat(failed.errorMessage()).as("상세는 자르지 않는다").isEqualTo(error);
		assertThat(failed.movement().items()).isEmpty();
	}

	private int stored(String table, String analysisId) {
		return jdbc.queryForObject("SELECT count(*) FROM " + table + " WHERE analysis_id = ?",
				Integer.class, analysisId);
	}
}
