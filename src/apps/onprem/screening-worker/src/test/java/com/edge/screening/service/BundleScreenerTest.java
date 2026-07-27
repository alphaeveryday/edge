package com.edge.screening.service;

import com.edge.screening.entity.PolicyVersion;
import com.edge.screening.entity.ReceivedBundle;
import com.edge.screening.entity.ScreeningRule;
import com.edge.screening.repository.AnalysisItemRepository;
import com.edge.screening.repository.PendingBundleRepository;
import com.edge.screening.repository.PolicyRepository;
import com.edge.screening.repository.PublicationRepository;
import com.edge.screening.repository.ScreeningCheckRepository;
import com.edge.screening.repository.ScreeningRuleRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.function.Executable;

import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;

/**
 * 상태 분기 계약(state-machine.md·ADR-0041)을 검증한다:
 * NEW·CORRECTION 정정분=활성 정책 평가(AUTO_PUBLISHED/REVIEW_REQUIRED/BLOCKED, 게시는
 * AUTO_PUBLISHED 만·근거는 screening_check) / 활성 정책 0건=NEW·CORRECTION 진행 중단
 * (무효화는 정책 무관 진행) / CORRECTION=구 리비전 종결·비노출 + supersedes 연결 /
 * INVALIDATION=즉시 비노출 / 형상 위반=마킹 없이 실패.
 */
class BundleScreenerTest {

	private static final String RESULT = """
			{"explanation_result_id":"er-1","etf_instrument_id":"i-1","etf_ticker":"069500",
			 "etf_name":"KODEX 200","trade_date":"2026-07-15","explanation_as_of":"2026-07-15T16:00:00+09:00",
			 "explanation_type":"EVENT_SUPPORTED","summary":"s","confidence_level":"MEDIUM"}""";

	private static class RecordingItems implements AnalysisItemRepository {
		record Upserted(String id, String supersedes, String reason, String status) {
		}

		final List<Upserted> upserts = new ArrayList<>();
		final List<String> transitions = new ArrayList<>();

		@Override
		public int upsert(String id, String inst, String ticker, String name, LocalDate tradeDate,
				OffsetDateTime asOf, String type, String summary, String headline, String confidence,
				String threadId, String evidencesJson, String supersedesItemId, String correctionReason,
				long sourceCursor, String status) {
			upserts.add(new Upserted(id, supersedesItemId, correctionReason, status));
			return 1;
		}

		@Override
		public int transition(String id, String status) {
			transitions.add(id + ":" + status);
			return 1;
		}
	}

	private static final class RecordingPublications implements PublicationRepository {
		final List<String> published = new ArrayList<>();
		final List<String> transitions = new ArrayList<>();

		@Override
		public int publish(String analysisItemId, String etfTicker, LocalDate tradeDate) {
			published.add(analysisItemId);
			return 1;
		}

		@Override
		public int transitionByItem(String analysisItemId, String status) {
			transitions.add(analysisItemId + ":" + status);
			return 1;
		}
	}

	private static final class RecordingPending implements PendingBundleRepository {
		final List<Long> screened = new ArrayList<>();

		// screen() 경로만 검증하므로 조회는 안 쓴다 — 빈 목록 스텁.
		@Override
		public List<ReceivedBundle> findUnscreenedRows(int limit) {
			return List.of();
		}

		@Override
		public void markScreened(long cursorFrom) {
			screened.add(cursorFrom);
		}
	}

	/** 판정 근거 기록 대역 — screening_check append 를 문자열로 수집한다. */
	private static final class RecordingChecks implements ScreeningCheckRepository {
		final List<String> appended = new ArrayList<>();

		@Override
		public void append(String analysisItemId, long policyVersionId, Long screeningRuleId,
				String result, String matchedText) {
			appended.add(analysisItemId + ":" + result + ":" + screeningRuleId + ":" + matchedText);
		}
	}

	private RecordingItems items;
	private RecordingPublications publications;
	private RecordingPending pending;
	private RecordingChecks checks;
	private Optional<PolicyVersion> activePolicy;
	private List<ScreeningRule> rules;
	private BundleScreener screener;

	@BeforeEach
	void setUp() {
		items = new RecordingItems();
		publications = new RecordingPublications();
		pending = new RecordingPending();
		checks = new RecordingChecks();
		// 기본 대역 = 관대한 활성 정책(자동 제공 ON·룰 없음) — 기존 NEW 자동 게시 케이스 유지.
		activePolicy = Optional.of(new PolicyVersion(10L, true, null));
		rules = List.of();
		PolicyRepository policies = () -> activePolicy;
		ScreeningRuleRepository ruleRepo = versionId -> rules;
		screener = new BundleScreener(pending, items, publications, policies, ruleRepo, checks);
	}

	private static byte[] bundle(String entries) {
		return ("{\"cursor_from\":1,\"cursor_to\":9,\"entries\":[" + entries + "]}")
				.getBytes(StandardCharsets.UTF_8);
	}

	@Test
	void NEW는_AUTO_PUBLISHED로_적재되고_자동_게시된다() {
		// WHY: walking skeleton 정책 = 무조건 통과. 게시돼야 Publication API 가 서빙한다.
		screener.screen(1, bundle("{\"cursor\":1,\"delivery_type\":\"NEW\",\"explanation_result\":" + RESULT + "}"));

		assertThat(items.upserts).containsExactly(new RecordingItems.Upserted("er-1", null, null, "AUTO_PUBLISHED"));
		assertThat(publications.published).containsExactly("er-1");
		assertThat(pending.screened).containsExactly(1L);
	}

	@Test
	void 활성_정책이_없으면_NEW는_마킹_없이_실패한다() {
		// WHY: screening_check.policy_version_id 는 NOT NULL — 정책 없이 상태를 정하면
		// 감사 근거 없는 전이가 된다. 정책 부재 = 진행 중단(DDL 주석 확정), 발행 후 재시도.
		activePolicy = Optional.empty();

		Executable call = () -> screener.screen(1,
				bundle("{\"cursor\":1,\"delivery_type\":\"NEW\",\"explanation_result\":" + RESULT + "}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(pending.screened).isEmpty();
		assertThat(items.upserts).isEmpty();
	}

	@Test
	void 활성_정책이_없어도_INVALIDATION은_진행된다() {
		// WHY: 무효화는 잘못된 노출을 걷어내는 안전 조치다 — 온보딩(정책 발행) 전이라는
		// 이유로 비노출이 멈추면 보수적 방향이 뒤집힌다.
		activePolicy = Optional.empty();

		screener.screen(3, bundle("{\"cursor\":3,\"delivery_type\":\"INVALIDATION\"," +
				"\"target_explanation_result_id\":\"er-2\"}"));

		assertThat(items.transitions).containsExactly("er-2:INVALIDATED");
		assertThat(pending.screened).containsExactly(3L);
	}

	@Test
	void BLOCK_룰에_걸린_NEW는_BLOCKED로_적재되고_게시되지_않는다() {
		// WHY: 차단 판정이 게시를 막지 못하면 정책이 장식이 된다 — 게시는 AUTO_PUBLISHED 전용.
		rules = List.of(new ScreeningRule(1L, 10L, "BANNED_WORD", "{\"text\":\"급등 확실\"}", "BLOCK", true));
		String risky = RESULT.replace("\"summary\":\"s\"", "\"summary\":\"급등 확실 전망\"");

		screener.screen(1, bundle("{\"cursor\":1,\"delivery_type\":\"NEW\",\"explanation_result\":" + risky + "}"));

		assertThat(items.upserts).containsExactly(new RecordingItems.Upserted("er-1", null, null, "BLOCKED"));
		assertThat(publications.published).isEmpty();
		assertThat(checks.appended).containsExactly("er-1:BLOCK:1:급등 확실");
	}

	@Test
	void 자동_제공_스위치_OFF_정책은_NEW를_검수_대기로_보낸다() {
		// WHY: 온보딩 기본값 = AUTO_PUBLISHED 0%(전건 검수, 티켓 확정). 스위치 OFF 근거는
		// 룰 무관(rule_id NULL) REVIEW 행으로 남는다.
		activePolicy = Optional.of(new PolicyVersion(10L, false, null));

		screener.screen(1, bundle("{\"cursor\":1,\"delivery_type\":\"NEW\",\"explanation_result\":" + RESULT + "}"));

		assertThat(items.upserts)
				.containsExactly(new RecordingItems.Upserted("er-1", null, null, "REVIEW_REQUIRED"));
		assertThat(publications.published).isEmpty();
		assertThat(checks.appended).containsExactly("er-1:REVIEW:null:null");
	}

	@Test
	void 청정_통과_NEW는_PASS_근거와_함께_게시된다() {
		// WHY: 자동 게시에도 "어느 정책으로 통과했나"(PASS 행)가 남아야 민원 재현이 온전하다.
		screener.screen(1, bundle("{\"cursor\":1,\"delivery_type\":\"NEW\",\"explanation_result\":" + RESULT + "}"));

		assertThat(items.upserts).containsExactly(new RecordingItems.Upserted("er-1", null, null, "AUTO_PUBLISHED"));
		assertThat(publications.published).containsExactly("er-1");
		assertThat(checks.appended).containsExactly("er-1:PASS:null:null");
	}

	@Test
	void 재수신_NEW는_판정_기록도_게시도_남기지_않는다() {
		// WHY: upsert 0행 = 이미 판정된 항목의 멱등 재수신 — check 를 또 쌓으면 append-only
		// 감사 원장에 같은 판정이 중복돼 재현이 오염된다.
		items = new RecordingItems() {
			@Override
			public int upsert(String id, String inst, String ticker, String name, LocalDate tradeDate,
					OffsetDateTime asOf, String type, String summary, String headline, String confidence,
					String threadId, String evidencesJson, String supersedesItemId, String correctionReason,
					long sourceCursor, String status) {
				super.upsert(id, inst, ticker, name, tradeDate, asOf, type, summary, headline, confidence,
						threadId, evidencesJson, supersedesItemId, correctionReason, sourceCursor, status);
				return 0;
			}
		};
		screener = new BundleScreener(pending, items, publications, () -> activePolicy, versionId -> rules, checks);

		screener.screen(1, bundle("{\"cursor\":1,\"delivery_type\":\"NEW\",\"explanation_result\":" + RESULT + "}"));

		assertThat(checks.appended).isEmpty();
		assertThat(publications.published).isEmpty();
		assertThat(pending.screened).containsExactly(1L);
	}

	@Test
	void CORRECTION은_구_리비전을_종결하고_정정분은_정책_평가를_거친다() {
		// WHY(결정 변경 2026-07-27): 정정분도 신규와 동일한 정책 평가 — 온보딩 철학
		// (기본 자동 제공, 걸린 것만 검수)의 일관 적용. 청정 정정은 자동 게시되고,
		// 구 리비전 종결·supersedes 연결·원장 보존은 불변이다.
		String corrected = RESULT.replace("er-1", "er-2");
		screener.screen(2, bundle("{\"cursor\":2,\"delivery_type\":\"CORRECTION\"," +
				"\"target_explanation_result_id\":\"er-1\",\"reason\":\"근거 공시 정정\"," +
				"\"explanation_result\":" + corrected + "}"));

		assertThat(items.transitions).containsExactly("er-1:CORRECTED");
		assertThat(publications.transitions).containsExactly("er-1:UNPUBLISHED");
		assertThat(items.upserts).containsExactly(
				new RecordingItems.Upserted("er-2", "er-1", "근거 공시 정정", "AUTO_PUBLISHED"));
		assertThat(publications.published).containsExactly("er-2");   // 내려간 grain 에 재게시
		assertThat(checks.appended).containsExactly("er-2:PASS:null:null");
	}

	@Test
	void BLOCK_룰에_걸린_정정분은_BLOCKED로_적재되고_게시되지_않는다() {
		rules = List.of(new ScreeningRule(1L, 10L, "BANNED_WORD", "{\"text\":\"급등 확실\"}", "BLOCK", true));
		String corrected = RESULT.replace("er-1", "er-2")
				.replace("\"summary\":\"s\"", "\"summary\":\"급등 확실 정정\"");

		screener.screen(2, bundle("{\"cursor\":2,\"delivery_type\":\"CORRECTION\"," +
				"\"target_explanation_result_id\":\"er-1\",\"reason\":\"정정\"," +
				"\"explanation_result\":" + corrected + "}"));

		assertThat(items.upserts).containsExactly(
				new RecordingItems.Upserted("er-2", "er-1", "정정", "BLOCKED"));
		assertThat(publications.published).isEmpty();
		assertThat(checks.appended).containsExactly("er-2:BLOCK:1:급등 확실");
	}

	@Test
	void 활성_정책이_없어도_CORRECTION은_비노출을_수행하고_정정분을_검수로_보존한다() {
		// WHY: 정정의 1순위는 틀린 문구를 내리는 것(안전 조치)이다 — 정책이 비활성화된
		// 구간에 정정이 막히면 틀린 게시가 계속 노출된다. 정정분은 판정할 정책이 없으니
		// 자동 노출 없이 REVIEW_REQUIRED 로 보존한다(check 는 정책 부재로 미기록 — 로그 표면화).
		activePolicy = Optional.empty();

		screener.screen(2, bundle("{\"cursor\":2,\"delivery_type\":\"CORRECTION\"," +
				"\"target_explanation_result_id\":\"er-1\",\"reason\":\"정정\"," +
				"\"explanation_result\":" + RESULT.replace("er-1", "er-2") + "}"));

		assertThat(items.transitions).containsExactly("er-1:CORRECTED");
		assertThat(publications.transitions).containsExactly("er-1:UNPUBLISHED");
		assertThat(items.upserts).containsExactly(
				new RecordingItems.Upserted("er-2", "er-1", "정정", "REVIEW_REQUIRED"));
		assertThat(publications.published).isEmpty();
		assertThat(checks.appended).isEmpty();
		assertThat(pending.screened).containsExactly(2L);
	}

	@Test
	void INVALIDATION은_항목과_게시분을_즉시_비노출한다() {
		screener.screen(3, bundle("{\"cursor\":3,\"delivery_type\":\"INVALIDATION\"," +
				"\"target_explanation_result_id\":\"er-2\",\"reason\":\"오탐지\"}"));

		assertThat(items.transitions).containsExactly("er-2:INVALIDATED");
		assertThat(publications.transitions).containsExactly("er-2:INVALIDATED");
	}

	@Test
	void 빈_entries_번들은_마킹_없이_실패한다() {
		// WHY: 와이어 계약은 minItems=1(빈 번들은 만들지 않는다 — 신규 없음은 204). 빈
		// entries 를 성공 처리하면 그 cursor 구간이 "정상 점검됨"으로 영구 마킹돼
		// 계약 위반이 은폐된다(Rule 12).
		Executable call = () -> screener.screen(8, bundle(""));

		assertThrows(IllegalStateException.class, call);
		assertThat(pending.screened).isEmpty();
	}

	@Test
	void 정체성_필드가_문자열이_아니면_마킹_없이_실패한다() {
		// WHY: Jackson 의 asString 은 숫자를 "123" 으로 강제한다 — 숫자 target 이 조용히
		// 문자열이 되면 전이 0행 + 경고만 남기고 번들이 마킹돼, malformed 무효화가
		// 재시도 없이 영구 소화된다(안티커럽션 계층이 막아야 할 강제 통과).
		Executable invalidation = () -> screener.screen(9,
				bundle("{\"cursor\":9,\"delivery_type\":\"INVALIDATION\",\"target_explanation_result_id\":123}"));
		Executable brokenNew = () -> screener.screen(10,
				bundle("{\"cursor\":10,\"delivery_type\":\"NEW\",\"explanation_result\":" +
						RESULT.replace("\"er-1\"", "123") + "}"));

		assertThrows(IllegalStateException.class, invalidation);
		assertThrows(IllegalStateException.class, brokenNew);
		assertThat(pending.screened).isEmpty();
	}

	@Test
	void 노출_문면_필드가_문자열이_아니면_마킹_없이_실패한다() {
		// WHY: summary 는 정책 매칭 대상이자 고객 노출 문면이다 — 숫자가 "123" 으로
		// 강제되면 금칙어 게이트가 원본 malformed 를 못 보고 그대로 게시까지 간다.
		Executable call = () -> screener.screen(12,
				bundle("{\"cursor\":12,\"delivery_type\":\"NEW\",\"explanation_result\":" +
						RESULT.replace("\"summary\":\"s\"", "\"summary\":123") + "}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(pending.screened).isEmpty();
	}

	@Test
	void source_events_요소가_객체가_아니면_실패한다() {
		// WHY: 출처 수는 정책 게이트(SINGLE_SOURCE·min_source_count)의 입력이다 —
		// [null,null] 이 2건으로 세지면 malformed 근거가 출처 기준을 통과한다.
		Executable call = () -> screener.screen(13,
				bundle("{\"cursor\":13,\"delivery_type\":\"NEW\",\"source_events\":[null,null]," +
						"\"explanation_result\":" + RESULT + "}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(pending.screened).isEmpty();
	}

	@Test
	void source_events_요소에_source_event_id가_없으면_실패한다() {
		// WHY: 런타임엔 JSON Schema 검증 계층이 없다 — 식별 불가 요소({})가 출처로
		// 세지면 출처 게이트(SINGLE_SOURCE·min_source_count)가 malformed 로 충족된다.
		Executable call = () -> screener.screen(15,
				bundle("{\"cursor\":15,\"delivery_type\":\"NEW\",\"source_events\":[{}]," +
						"\"explanation_result\":" + RESULT + "}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(pending.screened).isEmpty();
	}

	@Test
	void 중복_source_event_id는_출처_1건으로_센다() {
		// WHY: 출처 수는 자동 게시 임계의 입력이다 — 같은 출처가 두 번 실려 2건으로
		// 세지면 단일 출처 콘텐츠가 검수 없이 자동 게시된다(와이어 스키마는 uniqueItems 미보장).
		activePolicy = Optional.of(new PolicyVersion(10L, true, 2));

		screener.screen(17, bundle("{\"cursor\":17,\"delivery_type\":\"NEW\"," +
				"\"source_events\":[{\"source_event_id\":\"se-1\"},{\"source_event_id\":\"se-1\"}]," +
				"\"explanation_result\":" + RESULT + "}"));

		assertThat(items.upserts)
				.containsExactly(new RecordingItems.Upserted("er-1", null, null, "REVIEW_REQUIRED"));
		assertThat(publications.published).isEmpty();
	}

	@Test
	void etf_instrument_id가_문자열이_아니면_실패한다() {
		// WHY: 도메인 식별자다 — 숫자가 "123" 으로 강제되면 조작된 정체성이 원장에
		// 확정된다(explanation_result_id 와 동일한 강제 통과 차단).
		Executable call = () -> screener.screen(16,
				bundle("{\"cursor\":16,\"delivery_type\":\"NEW\",\"explanation_result\":" +
						RESULT.replace("\"etf_instrument_id\":\"i-1\"", "\"etf_instrument_id\":11") + "}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(pending.screened).isEmpty();
	}

	@Test
	void cursor가_long_범위를_벗어나면_실패한다() {
		// WHY: source_cursor 는 수신 원본↔항목의 감사 키다 — 오버플로 손실 변환된 값이
		// 조용히 저장되면 추적 관계가 원본과 어긋난 채 확정된다.
		Executable call = () -> screener.screen(14,
				bundle("{\"cursor\":9223372036854775808,\"delivery_type\":\"NEW\",\"explanation_result\":" +
						RESULT + "}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(pending.screened).isEmpty();
	}

	@Test
	void params_text가_문자열이_아닌_룰은_실패한다() {
		// WHY: {"text": true} 가 "true" 매칭룰로 조용히 동작하면 잘못 구성된 정책이
		// 무력화된 채 정상인 척한다(Rule 12) — 설정 결함은 판정 전에 드러나야 한다.
		rules = List.of(new ScreeningRule(1L, 10L, "BANNED_WORD", "{\"text\":true}", "BLOCK", true));

		Executable call = () -> screener.screen(11,
				bundle("{\"cursor\":11,\"delivery_type\":\"NEW\",\"explanation_result\":" + RESULT + "}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(pending.screened).isEmpty();
	}

	@Test
	void 미지의_delivery_type은_마킹_없이_실패한다() {
		// WHY: 새 전달 유형이 조용히 소화되면(스킵+마킹) 그 번들의 의미가 영영 유실된다(Rule 12).
		Executable call = () -> screener.screen(4,
				bundle("{\"cursor\":4,\"delivery_type\":\"PURGE\"}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(pending.screened).isEmpty();
	}

	@Test
	void cursor_없는_entry는_마킹_없이_실패한다() {
		// WHY: source_cursor 는 감사 추적(수신 원본 ↔ 항목) 필수 — 0 으로 조용히 저장되면
		// 추적 관계가 영구 유실된다.
		Executable call = () -> screener.screen(6,
				bundle("{\"delivery_type\":\"NEW\",\"explanation_result\":" + RESULT + "}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(pending.screened).isEmpty();
	}

	@Test
	void evidences가_배열이_아니면_실패한다() {
		// WHY: 형상 위반을 빈 배열로 치환하면 근거가 조용히 사라진다 — 계약 위반은 표면화.
		Executable call = () -> screener.screen(7,
				bundle("{\"cursor\":7,\"delivery_type\":\"NEW\",\"evidences\":\"oops\"," +
						"\"explanation_result\":" + RESULT + "}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(pending.screened).isEmpty();
	}

	@Test
	void ticker_결측_NEW는_적재만_하고_게시하지_않는다() {
		// WHY: ticker 는 서빙 키 — 없으면 노출 불가. 수신은 보존(원본 유실 금지), 노출만 보류.
		String noTicker = RESULT.replace("\"etf_ticker\":\"069500\",\n", "");
		screener.screen(5, bundle("{\"cursor\":5,\"delivery_type\":\"NEW\",\"explanation_result\":" +
				noTicker.replace("\"etf_ticker\":\"069500\",", "") + "}"));

		assertThat(items.upserts).hasSize(1);
		assertThat(publications.published).isEmpty();
	}
}
