package com.edge.tenantconsole.repository;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.AbstractPostgresIntegrationTest;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.model.Explanation;
import com.edge.tenantconsole.model.FeedStatus;
import com.edge.tenantconsole.service.ExplanationService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * explanations 읽기 표면(ALPHA-607)의 DB 계약을 실 Postgres 로 검증한다 — 손 대역이 우회하는
 * 실제 의미가 WHY(Rule 9): evidences JSONB 파싱, 검수 사유 파생(screening_check REVIEW·BLOCK
 * → rule_type→UI 어휘), 최종 문구의 publication.published_summary 스냅샷, 상태 필터
 * (RECEIVED·INVALIDATED 제외), 반입 집계. 쓰기 표면(사후 운영 전이·감사)의 DB 계약은
 * ExplanationWriteIT 가 담당한다(ALPHA-613). 시드는 테스트 한정 JdbcTemplate, id 는
 * it607- 접두로 격리하고, 목록은 공유 컨테이너 오염을 피해 내 id 로만 단언한다.
 */
class ExplanationSurfaceIT extends AbstractPostgresIntegrationTest {

	@Autowired
	private ExplanationService explanations;
	@Autowired
	private JdbcTemplate jdbc;

	private long cursor = 60700;

	@BeforeEach
	void resetServingScope() {
		// head 판정(serving)은 serving_scope 게이트를 포함한다 — ScopeIT 등 다른 클래스가
		// 공유 컨테이너에 남긴 토글(MARKET XKRX OFF 는 전역 차단)이 실행 순서에 따라 head
		// 단언을 무너뜨리지 않게 클래스 진입마다 비운다(ScopeIT 의 격리 패턴과 동일).
		jdbc.update("DELETE FROM serving_scope");
	}

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
		seedItem(id, ticker, name, status, confidence, summary, evidencesJson, receivedAt,
				OffsetDateTime.now());
	}

	/** as_of 지정 오버로드 — 다스냅샷 공존(head 판정) 테스트가 스냅샷 축을 고정한다(ALPHA-744). */
	private void seedItem(String id, String ticker, String name, String status, String confidence,
			String summary, String evidencesJson, OffsetDateTime receivedAt, OffsetDateTime asOf) {
		jdbc.update("""
				INSERT INTO analysis_item (explanation_result_id, etf_instrument_id, etf_ticker,
				    etf_name, trade_date, explanation_as_of, explanation_type, summary,
				    confidence_level, status, source_cursor, evidences, received_at)
				VALUES (?, 'i-607', ?, ?, '2026-07-15', ?, 'EVENT_SUPPORTED', ?, ?, ?, ?,
				    CAST(? AS jsonb), ?)
				""", id, ticker, name, asOf, summary, confidence, status, cursor++, evidencesJson,
				receivedAt);
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
		return explanations.list(100, 0).stream().filter(e -> e.id().equals(id)).findFirst().orElseThrow();
	}

	@Test
	void 목록은_원장을_UI_도메인으로_매핑하고_사유를_파생한다() {
		long assertive = seedActiveRule("ASSERTIVE_EXPRESSION", "REVIEW");
		seedItem("it607-review", "607REV", "에코프로비엠", "REVIEW_REQUIRED", "HIGH", "원본 요약",
				"[{\"kind\":\"DISCLOSURE\",\"title\":\"공급 계약\",\"source\":\"DART\","
						+ "\"published_at\":\"2026-07-14T09:00:00Z\","
						+ "\"source_uri\":\"https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260714000001\"}]",
				OffsetDateTime.now());
		seedCheck("it607-review", assertive, "REVIEW");

		Explanation it = find("it607-review");

		assertThat(it.name()).isEqualTo("에코프로비엠");
		assertThat(it.code()).isEqualTo("607REV");
		assertThat(it.status()).isEqualTo("REVIEW_REQUIRED");
		assertThat(it.confidence()).isEqualTo("HIGH");          // confidence_level 원값 투영
		assertThat(it.reviewReason()).isEqualTo("ASSERTIVE");   // ASSERTIVE_EXPRESSION → UI 어휘
		assertThat(it.original()).isEqualTo("원본 요약");
		assertThat(it.finalText()).isEqualTo("원본 요약");        // 게시 스냅샷 없으면 summary
		assertThat(it.evidence()).singleElement().satisfies(e -> {
			assertThat(e.kind()).isEqualTo("DISCLOSURE");       // dto 가 공시로 번역
			assertThat(e.source()).isEqualTo("DART");
			assertThat(e.publishedAt()).isNotNull();
			// JSONB source_uri → 파서(parseEvidence) 실경로 검증(ALPHA-739) — dto 테스트는
			// 모델을 직접 만들어 파서를 우회하므로 키 오타 회귀는 여기서만 잡힌다.
			assertThat(e.sourceUri())
					.isEqualTo("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260714000001");
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
				+ "explanation_as_of, published_summary, status, unpublished_at) "
				+ "VALUES ('it607-unpub', '607UNP', '2026-07-15', "
				+ "'2026-07-15T16:00:00+09:00', '중단 전 게시 문구', 'UNPUBLISHED', now())");

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
				+ "explanation_as_of, published_summary, status, unpublished_at) "
				+ "VALUES ('it607-relast', '607RLP', '2026-07-15', "
				+ "'2026-07-15T14:00:00+09:00', '구 게시 문구', 'UNPUBLISHED', now())");
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "explanation_as_of, published_summary) VALUES ('it607-relast', '607RLP', "
				+ "'2026-07-15', '2026-07-15T16:00:00+09:00', '신 게시 문구')");

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
				+ "explanation_as_of, published_summary) VALUES ('it607-pub', '607PUB', "
				+ "'2026-07-15', '2026-07-15T16:00:00+09:00', '검수 편집 문구')");

		Explanation it = find("it607-pub");
		assertThat(it.original()).isEqualTo("모델 원본 문구");    // original 은 summary 그대로
		assertThat(it.finalText()).isEqualTo("검수 편집 문구");    // final 은 게시 스냅샷 우선
	}

	@Test
	void 수신전_무효화_항목은_목록에서_빠진다() {
		seedItem("it607-received", "607RCV", "카카오", "RECEIVED", "LOW", "요약", "[]",
				OffsetDateTime.now());
		seedItem("it607-invalidated", "607INV", "네이버", "INVALIDATED", "LOW", "요약", "[]",
				OffsetDateTime.now());

		List<String> ids = explanations.list(100, 0).stream().map(Explanation::id).toList();
		assertThat(ids).doesNotContain("it607-received", "it607-invalidated");
	}

	@Test
	void 노출_head_는_같은_티커_다스냅샷_중_유효_최신이다() {
		OffsetDateTime older = OffsetDateTime.parse("2026-07-15T14:00:00+09:00");
		OffsetDateTime newer = OffsetDateTime.parse("2026-07-15T16:00:00+09:00");
		seedItem("it607-head-old", "607HDA", "포스코", "AUTO_PUBLISHED", "LOW", "구 스냅샷", "[]",
				OffsetDateTime.now(), older);
		seedItem("it607-head-new", "607HDA", "포스코", "AUTO_PUBLISHED", "LOW", "신 스냅샷", "[]",
				OffsetDateTime.now(), newer);
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "explanation_as_of) VALUES ('it607-head-old', '607HDA', '2026-07-15', ?)", older);
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "explanation_as_of) VALUES ('it607-head-new', '607HDA', '2026-07-15', ?)", newer);

		// WHY: 공존(ALPHA-743) 후 배지는 서빙 진실(유효 최신 승리)과 일치해야 한다 — head 아닌
		// 항목의 중단을 "노출을 끊었다"로 오인하는 게 이 티켓(ALPHA-744)이 막는 사고다.
		assertThat(find("it607-head-new").serving()).isTrue();
		assertThat(find("it607-head-old").serving()).isFalse();
	}

	@Test
	void head_무효화_시_직전_스냅샷이_노출_head_다() {
		OffsetDateTime older = OffsetDateTime.parse("2026-07-15T14:00:00+09:00");
		OffsetDateTime newer = OffsetDateTime.parse("2026-07-15T16:00:00+09:00");
		seedItem("it607-fb-old", "607FBK", "한화", "AUTO_PUBLISHED", "LOW", "직전 스냅샷", "[]",
				OffsetDateTime.now(), older);
		seedItem("it607-fb-new", "607FBK", "한화", "INVALIDATED", "LOW", "무효화 스냅샷", "[]",
				OffsetDateTime.now(), newer);
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "explanation_as_of) VALUES ('it607-fb-old', '607FBK', '2026-07-15', ?)", older);
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "explanation_as_of, status) VALUES ('it607-fb-new', '607FBK', '2026-07-15', ?, "
				+ "'INVALIDATED')", newer);

		// WHY: head 는 as_of 파생이 아니라 유효(PUBLISHED) 최신이어야 한다 — as_of 만 보면
		// 무효화 fallback(직전 스냅샷 재노출, ADR-0045 결정 3)에서 죽은 판을 head 로 가리킨다.
		assertThat(find("it607-fb-old").serving()).isTrue();
	}

	@Test
	void 게시본_없는_항목은_as_of_최신이어도_노출_head_가_아니다() {
		OffsetDateTime older = OffsetDateTime.parse("2026-07-15T14:00:00+09:00");
		OffsetDateTime newer = OffsetDateTime.parse("2026-07-15T16:00:00+09:00");
		seedItem("it607-ghost-pub", "607GHO", "두산", "AUTO_PUBLISHED", "LOW", "게시된 스냅샷", "[]",
				OffsetDateTime.now(), older);
		seedItem("it607-ghost-new", "607GHO", "두산", "AUTO_PUBLISHED", "LOW", "게시본 없는 스냅샷",
				"[]", OffsetDateTime.now(), newer);
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "explanation_as_of) VALUES ('it607-ghost-pub', '607GHO', '2026-07-15', ?)", older);

		// WHY: 항목 상태·as_of 만으로 head 를 파생하면 게시본 없는 유령 항목(ALPHA-724 계열)이
		// head 로 표시된다 — 고객 화면의 진실은 publication 이므로 배지는 게시본 실재에 근거해야
		// 한다. 무효화 fallback IT 는 INVALIDATED 항목이 목록에서 빠져 이 반례를 못 잡는다.
		assertThat(find("it607-ghost-pub").serving()).isTrue();
		assertThat(find("it607-ghost-new").serving()).isFalse();
	}

	@Test
	void 노출_head_는_as_of_보다_거래일이_우선한다() {
		seedItem("it607-day-old", "607DAY", "효성", "AUTO_PUBLISHED", "LOW", "전일 늦은 스냅샷",
				"[]", OffsetDateTime.now(), OffsetDateTime.parse("2026-07-14T16:00:00+09:00"));
		seedItem("it607-day-new", "607DAY", "효성", "AUTO_PUBLISHED", "LOW", "당일 이른 스냅샷",
				"[]", OffsetDateTime.now(), OffsetDateTime.parse("2026-07-15T10:00:00+09:00"));
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "explanation_as_of) VALUES ('it607-day-old', '607DAY', '2026-07-14', "
				+ "'2026-07-14T16:00:00+09:00')");
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "explanation_as_of) VALUES ('it607-day-new', '607DAY', '2026-07-15', "
				+ "'2026-07-15T10:00:00+09:00')");

		// WHY: 서빙 정렬(SSOT)은 trade_date → as_of 순이다 — as_of 우선으로 전사가 어긋나면
		// 전일의 늦은 스냅샷(16:00)이 당일 판(10:00)을 제치고 head 로 표시된다.
		assertThat(find("it607-day-new").serving()).isTrue();
		assertThat(find("it607-day-old").serving()).isFalse();
	}

	@Test
	void head_판정은_게시본_상태와_항목_상태_게이트를_각각_요구한다() {
		OffsetDateTime head = OffsetDateTime.parse("2026-07-15T12:00:00+09:00");
		seedItem("it607-gate-head", "607GAT", "한전", "AUTO_PUBLISHED", "LOW", "정상 head", "[]",
				OffsetDateTime.now(), head);
		seedItem("it607-gate-pub", "607GAT", "한전", "AUTO_PUBLISHED", "LOW", "내려간 게시본", "[]",
				OffsetDateTime.now(), OffsetDateTime.parse("2026-07-15T16:00:00+09:00"));
		seedItem("it607-gate-item", "607GAT", "한전", "REJECTED", "LOW", "비노출 항목 상태", "[]",
				OffsetDateTime.now(), OffsetDateTime.parse("2026-07-15T14:00:00+09:00"));
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "explanation_as_of) VALUES ('it607-gate-head', '607GAT', '2026-07-15', ?)", head);
		// p.status 게이트 반례 — 게시본이 내려갔으면(UNPUBLISHED) as_of 최신이어도 head 가 아니다.
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "explanation_as_of, status, unpublished_at) VALUES ('it607-gate-pub', '607GAT', "
				+ "'2026-07-15', '2026-07-15T16:00:00+09:00', 'UNPUBLISHED', now())");
		// a.status 게이트 반례 — 게시본이 PUBLISHED 여도 항목이 노출 상태가 아니면 head 가 아니다.
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "explanation_as_of) VALUES ('it607-gate-item', '607GAT', '2026-07-15', "
				+ "'2026-07-15T14:00:00+09:00')");

		// WHY: 서빙 술어(SSOT)는 p.status='PUBLISHED' × a.status IN(노출 상태) 두 게이트다.
		// 무효화 실플로우 픽스처는 둘을 함께 바꿔 상관되므로, 한 게이트가 빠진 오전사를 여기서
		// 각각 독립으로 잡는다(도달 희귀 상태여도 전사 충실성이 계약이다).
		assertThat(find("it607-gate-head").serving()).isTrue();
		assertThat(find("it607-gate-pub").serving()).isFalse();
		assertThat(find("it607-gate-item").serving()).isFalse();
	}

	@Test
	void 제공_범위에서_제외된_종목은_노출_head_가_아니다() {
		OffsetDateTime asOf = OffsetDateTime.parse("2026-07-15T12:00:00+09:00");
		seedItem("it607-scope-off", "607SCP", "롯데", "AUTO_PUBLISHED", "LOW", "제외 종목", "[]",
				OffsetDateTime.now(), asOf);
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "explanation_as_of) VALUES ('it607-scope-off', '607SCP', '2026-07-15', ?)", asOf);
		jdbc.update("INSERT INTO serving_scope (scope_type, scope_key, enabled) "
				+ "VALUES ('INSTRUMENT', '607SCP', false)");

		// WHY: publication-api 는 제공 범위 차단이면 게시분 조회 전에 204 로 수렴한다
		// (isServingBlocked) — 게시·항목 상태만 보면 고객이 못 보는 종목에 "노출 중" 배지가
		// 뜬다. MARKET(XKRX) 전역 토글은 같은 NOT EXISTS 분기이나 공유 컨테이너의 다른
		// 테스트를 전역 차단하므로 여기선 INSTRUMENT 로만 고정한다(전역 실효화는
		// publication-api ExplanationScopeIntegrationTest 소관).
		assertThat(find("it607-scope-off").serving()).isFalse();
	}

	/** WHY: 목록이 페이지 단위가 되면(ALPHA-914) 상세 딥링크는 목록 캐시에 의존할 수 없다 —
	 * 단건 조회가 목록과 같은 상태 필터를 적용해야 수신 전(RECEIVED) 항목이 딥링크로 새지 않는다. */
	@Test
	void 단건_조회는_노출_상태만_돌려주고_수신전_항목은_404_다() {
		seedItem("it914-one", "914ONE", "삼성전자", "APPROVED", "LOW", "요약", "[]",
				OffsetDateTime.now());
		seedItem("it914-rcv", "914RCV", "카카오", "RECEIVED", "LOW", "요약", "[]",
				OffsetDateTime.now());

		assertThat(explanations.detail("it914-one").id()).isEqualTo("it914-one");
		assertThatThrownBy(() -> explanations.detail("it914-rcv"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.EXPLANATION_NOT_FOUND));
	}

	/** WHY: 대시보드 KPI·검수 대기 배지는 로드된 페이지로 셀 수 없다(ALPHA-914) — 서버
	 * 집계가 노출 6종을 0 포함 전부 실어야 소비자가 결측과 0 을 구분할 필요가 없다. */
	@Test
	void 상태별_건수는_노출_6종을_0_포함_전부_싣는다() {
		seedItem("it914-cnt", "914CNT", "네이버", "BLOCKED", "LOW", "요약", "[]",
				OffsetDateTime.now());

		Map<String, Long> counts = explanations.statusCounts();
		assertThat(counts.keySet()).containsExactlyInAnyOrder("AUTO_PUBLISHED", "APPROVED",
				"REVIEW_REQUIRED", "BLOCKED", "REJECTED", "UNPUBLISHED");
		assertThat(counts.get("BLOCKED")).isGreaterThanOrEqualTo(1);
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

}
