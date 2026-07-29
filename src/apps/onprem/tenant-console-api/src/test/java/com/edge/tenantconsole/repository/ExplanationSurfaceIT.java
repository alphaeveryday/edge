package com.edge.tenantconsole.repository;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.AbstractPostgresIntegrationTest;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.model.Explanation;
import com.edge.tenantconsole.model.FeedStatus;
import com.edge.tenantconsole.service.ExplanationService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.OffsetDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * explanations 실전환(ALPHA-607)의 DB 계약을 실 Postgres 로 검증한다 — 손 대역이 우회하는
 * 실제 의미가 WHY(Rule 9): evidences JSONB 파싱, 검수 사유 파생(screening_check REVIEW·BLOCK
 * → rule_type→UI 어휘), 최종 문구의 publication.published_summary 스냅샷, 상태 필터
 * (RECEIVED·CORRECTED 제외), 반입 집계. 쓰기는 아직 mock 이라 원장 ID 는 404 라는 슬라이스
 * 경계도 함께 드러낸다(쓰기 전환에서 깨져야 정상). 시드는 테스트 한정 JdbcTemplate, id 는
 * it607- 접두로 격리하고, 목록은 공유 컨테이너 오염을 피해 내 id 로만 단언한다.
 */
class ExplanationSurfaceIT extends AbstractPostgresIntegrationTest {

	@Autowired
	private ExplanationService explanations;
	@Autowired
	private JdbcTemplate jdbc;

	private long cursor = 60700;

	private long seedActiveRule(String ruleType, String action) {
		jdbc.update("UPDATE policy_version SET deactivated_at = now() "
				+ "WHERE activated_at IS NOT NULL AND deactivated_at IS NULL");
		long version = jdbc.queryForObject(
				"INSERT INTO policy_version (version_no, disclaimer_text, activated_at) "
						+ "VALUES ((SELECT COALESCE(MAX(version_no),0)+1 FROM policy_version), '문구', now()) "
						+ "RETURNING policy_version_id", Long.class);
		return jdbc.queryForObject(
				"INSERT INTO screening_rule (policy_version_id, rule_type, params, action) "
						+ "VALUES (?, ?, '{}'::jsonb, ?) RETURNING screening_rule_id",
				Long.class, version, ruleType, action);
	}

	private void seedItem(String id, String ticker, String name, String status, String confidence,
			String summary, String evidencesJson, OffsetDateTime receivedAt) {
		jdbc.update("""
				INSERT INTO analysis_item (explanation_result_id, etf_instrument_id, etf_ticker,
				    etf_name, trade_date, explanation_as_of, explanation_type, summary,
				    confidence_level, status, source_cursor, evidences, received_at)
				VALUES (?, 'i-607', ?, ?, '2026-07-15', now(), 'EVENT_SUPPORTED', ?, ?, ?, ?,
				    CAST(? AS jsonb), ?)
				""", id, ticker, name, summary, confidence, status, cursor++, evidencesJson, receivedAt);
	}

	private void seedCheck(String itemId, long ruleId, String result) {
		long version = jdbc.queryForObject(
				"SELECT policy_version_id FROM screening_rule WHERE screening_rule_id = ?",
				Long.class, ruleId);
		jdbc.update("INSERT INTO screening_check (analysis_item_id, policy_version_id, "
				+ "screening_rule_id, result, matched_text) VALUES (?, ?, ?, ?, '근거')",
				itemId, version, ruleId, result);
	}

	private Explanation find(String id) {
		return explanations.list().stream().filter(e -> e.id().equals(id)).findFirst().orElseThrow();
	}

	@Test
	void 목록은_원장을_UI_도메인으로_매핑하고_사유를_파생한다() {
		long assertive = seedActiveRule("ASSERTIVE_EXPRESSION", "REVIEW");
		seedItem("it607-review", "607REV", "에코프로비엠", "REVIEW_REQUIRED", "HIGH", "원본 요약",
				"[{\"kind\":\"DISCLOSURE\",\"title\":\"공급 계약\",\"source\":\"DART\","
						+ "\"published_at\":\"2026-07-14T09:00:00Z\"}]",
				OffsetDateTime.now());
		seedCheck("it607-review", assertive, "REVIEW");

		Explanation it = find("it607-review");

		assertThat(it.name()).isEqualTo("에코프로비엠");
		assertThat(it.code()).isEqualTo("607REV");
		assertThat(it.status()).isEqualTo("REVIEW_REQUIRED");
		assertThat(it.risk()).isEqualTo("HIGH");                // confidence_level → risk
		assertThat(it.reviewReason()).isEqualTo("ASSERTIVE");   // ASSERTIVE_EXPRESSION → UI 어휘
		assertThat(it.original()).isEqualTo("원본 요약");
		assertThat(it.finalText()).isEqualTo("원본 요약");        // 게시 스냅샷 없으면 summary
		assertThat(it.evidence()).singleElement().satisfies(e -> {
			assertThat(e.kind()).isEqualTo("DISCLOSURE");       // dto 가 공시로 번역
			assertThat(e.source()).isEqualTo("DART");
			assertThat(e.publishedAt()).isNotNull();
		});
	}

	@Test
	void 차단_항목_사유는_상태분기_BLOCK_판정에서_나온다() {
		long assertive = seedActiveRule("ASSERTIVE_EXPRESSION", "REVIEW");
		long banned = seedActiveRule("BANNED_WORD", "BLOCK");
		seedItem("it607-blocked", "607BLK", "테슬라", "BLOCKED", "HIGH", "요약", "[]",
				OffsetDateTime.now());
		seedCheck("it607-blocked", assertive, "REVIEW");  // 먼저 기록된 이관 사유(낮은 check id)
		seedCheck("it607-blocked", banned, "BLOCK");      // 실제 차단 원인

		// WHY: 첫 check(REVIEW·ASSERTIVE)가 아니라 현재 상태 분기(BLOCKED→BLOCK)의 사유를
		// 취해야 한다 — 아니면 차단 항목이 실제 차단 원인 대신 이관 사유를 노출한다.
		assertThat(find("it607-blocked").reviewReason()).isEqualTo("BANNED_WORD");
	}

	@Test
	void 중단된_항목의_최종_문구는_마지막_게시본을_보존한다() {
		seedItem("it607-unpub", "607UNP", "카카오", "UNPUBLISHED", "LOW", "모델 원본", "[]",
				OffsetDateTime.now());
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "published_summary, status, unpublished_at) VALUES ('it607-unpub', '607UNP', "
				+ "'2026-07-15', '중단 전 게시 문구', 'UNPUBLISHED', now())");

		// WHY: 제공 중단돼 publication.status 가 PUBLISHED 가 아니어도 마지막 노출 문구를
		// final 로 보존해야 한다 — PUBLISHED 로만 좁히면 잃는다(기존 mock stop 도 finalText 유지).
		assertThat(find("it607-unpub").finalText()).isEqualTo("중단 전 게시 문구");
	}

	@Test
	void 최종_문구는_게시_이력_중_최신본을_고른다() {
		seedItem("it607-relast", "607RLP", "현대모비스", "APPROVED", "LOW", "모델 원본", "[]",
				OffsetDateTime.now());
		// 재게시 이력: 구본(작은 publication_id, UNPUBLISHED) → 신본(큰 id, PUBLISHED).
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "published_summary, status, unpublished_at) VALUES ('it607-relast', '607RLP', "
				+ "'2026-07-15', '구 게시 문구', 'UNPUBLISHED', now())");
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "published_summary) VALUES ('it607-relast', '607RLP', '2026-07-15', '신 게시 문구')");

		// WHY: 항목당 최신 게시본(publication_id 최대)이어야 한다 — 아니면 구본이 노출된다.
		assertThat(find("it607-relast").finalText()).isEqualTo("신 게시 문구");
	}

	@Test
	void 깨진_근거_시각은_항목을_무너뜨리지_않고_시각만_생략한다() {
		seedItem("it607-badjson", "607BAD", "네이버", "AUTO_PUBLISHED", "LOW", "요약",
				"[{\"kind\":\"NEWS\",\"title\":\"제목\",\"source\":\"X\",\"published_at\":\"어제\"}]",
				OffsetDateTime.now());

		// WHY: 근거 한 건의 불량 published_at 이 GET /explanations 전체를 500 시키면 안 된다 —
		// 항목 단위 격리로 시각만 생략하고 나머지는 보존한다(Rule 12 — 조용한 전면 실패 금지).
		assertThat(find("it607-badjson").evidence()).singleElement().satisfies(e -> {
			assertThat(e.kind()).isEqualTo("NEWS");
			assertThat(e.publishedAt()).isNull();
		});
	}

	@Test
	void 근거가_배열이_아니면_비운다() {
		// 유효 JSONB 이나 배열이 아닌 형상(객체 등) — 계약 위반이라 근거를 비우고(로그) 항목은 살린다.
		seedItem("it607-nonarr", "607NAR", "LG전자", "AUTO_PUBLISHED", "LOW", "요약", "{}",
				OffsetDateTime.now());

		assertThat(find("it607-nonarr").evidence()).isEmpty();
	}

	@Test
	void 배열_안_불량_근거_요소는_생략하고_유효분만_남긴다() {
		// [null, 유효] — null 요소는 kind/source 가 null 인 반쪽 근거가 되므로 생략, 유효분만.
		seedItem("it607-nullel", "607NUL", "기아", "AUTO_PUBLISHED", "LOW", "요약",
				"[null, {\"kind\":\"DISCLOSURE\",\"title\":\"공시\",\"source\":\"DART\"}]",
				OffsetDateTime.now());

		assertThat(find("it607-nullel").evidence()).singleElement()
				.satisfies(e -> assertThat(e.kind()).isEqualTo("DISCLOSURE"));
	}

	@Test
	void 이름_ticker_없는_항목은_표시_폴백을_준다() {
		// 미게시 항목은 etf_name·etf_ticker 가 결측일 수 있다(스키마 nullable) — UI 검색
		// toLowerCase 가 NPE 나지 않게 표시 폴백을 준다.
		jdbc.update("INSERT INTO analysis_item (explanation_result_id, etf_instrument_id, "
				+ "trade_date, explanation_as_of, explanation_type, summary, status, source_cursor, "
				+ "received_at) VALUES ('it607-noname', 'i-607', '2026-07-15', now(), "
				+ "'EVENT_SUPPORTED', '요약', 'REVIEW_REQUIRED', ?, now())", cursor++);

		Explanation it = find("it607-noname");
		assertThat(it.name()).isEqualTo("(이름 없음)");
		assertThat(it.code()).isEqualTo("—");
	}

	@Test
	void 승인된_항목은_과거_검수_사유를_노출하지_않는다() {
		long assertive = seedActiveRule("ASSERTIVE_EXPRESSION", "REVIEW");
		seedItem("it607-approved", "607APP", "삼성SDI", "APPROVED", "LOW", "요약", "[]",
				OffsetDateTime.now());
		seedCheck("it607-approved", assertive, "REVIEW");  // 승인 전 REVIEW 검사가 원장에 남아 있음

		// WHY: 승인 시 사유가 해소된다(기존 mock 불변식) — 사유는 검수 대기·차단·반려에만 붙는다.
		// 상태를 안 보고 과거 REVIEW 행을 취하면 승인 항목에 이관 사유가 되살아난다.
		assertThat(find("it607-approved").reviewReason()).isNull();
	}

	@Test
	void 게시된_항목의_최종_문구는_publication_스냅샷이다() {
		seedItem("it607-pub", "607PUB", "삼성전자", "APPROVED", "LOW", "모델 원본 문구", "[]",
				OffsetDateTime.now());
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "published_summary) VALUES ('it607-pub', '607PUB', '2026-07-15', '검수 편집 문구')");

		Explanation it = find("it607-pub");
		assertThat(it.original()).isEqualTo("모델 원본 문구");    // original 은 summary 그대로
		assertThat(it.finalText()).isEqualTo("검수 편집 문구");    // final 은 게시 스냅샷 우선
	}

	@Test
	void 수신전_정정_리비전은_목록에서_빠진다() {
		seedItem("it607-received", "607RCV", "카카오", "RECEIVED", "LOW", "요약", "[]",
				OffsetDateTime.now());
		seedItem("it607-corrected", "607COR", "네이버", "CORRECTED", "LOW", "요약", "[]",
				OffsetDateTime.now());

		List<String> ids = explanations.list().stream().map(Explanation::id).toList();
		assertThat(ids).doesNotContain("it607-received", "it607-corrected");
	}

	@Test
	void 반입_상태는_오늘_반입_수와_최근_시각을_집계한다() {
		seedItem("it607-feed", "607FED", "현대차", "AUTO_PUBLISHED", "LOW", "요약", "[]",
				OffsetDateTime.now());

		FeedStatus feed = explanations.feedStatus();
		assertThat(feed.lastReceivedAt()).isNotNull();
		assertThat(feed.todayReceived()).isGreaterThanOrEqualTo(1);
		assertThat(feed.state()).isEqualTo(FeedStatus.NORMAL);   // 방금 반입 → 정상
	}

	@Test
	void 실_설명_ID_쓰기는_아직_404다() {
		// WHY: 읽기는 원장, 쓰기는 mock — 실 ID 는 mock 에 없어 404 다. 이 테스트는 쓰기
		// 전환(원장 전이)에서 깨져야 정상이다. 조용한 성공 둔갑 방지(Rule 12).
		seedItem("it607-write", "607WRT", "SK하이닉스", "AUTO_PUBLISHED", "LOW", "요약", "[]",
				OffsetDateTime.now());

		assertThatThrownBy(() -> explanations.approve("it607-write", "문구"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.EXPLANATION_NOT_FOUND));
		assertThatThrownBy(() -> explanations.stop("it607-write"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.EXPLANATION_NOT_FOUND));
	}

	@Test
	void 쓰기_입력_검증은_원장_조회_전에_400이다() {
		assertThatThrownBy(() -> explanations.updateFinal("it607-any", ""))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.INVALID_REQUEST));
		assertThatThrownBy(() -> explanations.reject("it607-any", "  "))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.REASON_REQUIRED));
	}
}
