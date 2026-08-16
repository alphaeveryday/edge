package com.edge.publication;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.edge.common.exception.ExceptionAdvice;
import com.edge.publication.controller.ExplanationController;
import com.edge.publication.service.ExplanationService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.LocalDate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 콘솔 면책 문구 발행(policy_version.disclaimer_text)의 서빙단 실효화(ALPHA-772)를 실 Postgres 로
 * 검증한다. WHY: 문구가 저장만 되고 서빙이 이를 읽지 않으면 컴플라이언스가 통제 수단을 고쳐도
 * 고객 화면은 그대로다 — 콘솔은 성공을 보고하는데 실제로는 반영되지 않는 조용한 실패였다.
 *
 * <p>제공 범위 테스트와 달리 게시분 조회 캐시(ALPHA-433)를 <b>끄지 않는다</b>. 면책 문구 조회는
 * 그 캐시 밖(응답 조립 시점)이므로 캐시를 켠 채로 "발행 즉시 반영"이 성립하는지가 곧 검증 대상이다 —
 * 껐다면 캐시가 문구를 가리는 회귀를 이 테스트가 놓친다.
 *
 * <p>캐시를 켠 채 결정론을 얻기 위해 두 가지를 둔다. ① <b>전용 컨텍스트</b> — known-tickers 를
 * 이 클래스 전용 티커로 덮어 별도 Spring 컨텍스트(=별도 캐시 인스턴스)를 받는다. 기본 컨텍스트의
 * 캐시는 테스트 클래스 사이에 살아남아, 같은 (ticker, 거래일) 키를 쓰는 다른 클래스와 서로의
 * 시드를 덮어쓴다(PublicationRepositoryIntegrationTest 가 305720·2026-07-15 을 쓴다).
 * ② <b>클래스당 1회 시드</b> — 메서드마다 재시드하면 publication_id 가 바뀌는데 캐시는 이전 id 를
 * 든 채로 남아 죽은 게시분을 서빙할 수 있다. 메서드 간에 갈아끼우는 것은 정책 행뿐이고,
 * 그건 캐시 밖이라 안전하다.
 *
 * <p>기대 문자열을 상수 참조가 아니라 리터럴로 둔 것은 의도적이다 — 상수를 참조하면 상수 자체가
 * 콘솔과 어긋나도 통과한다. 이 리터럴은 콘솔 {@code ScreeningService.DEFAULT_DISCLAIMER} 와
 * 같아야 한다(두 기본값이 갈린 것이 이 버그의 발단이었다).
 */
class ExplanationDisclaimerIntegrationTest extends OnpremPostgresIntegrationTest {

	/** 이 클래스 전용 — 다른 테스트 클래스와 캐시 키가 겹치지 않게 known-tickers 를 이 값으로 덮는다. */
	private static final String TICKER = "379800";

	private static final LocalDate TRADE_DATE = LocalDate.of(2026, 7, 15);

	/** 콘솔이 첫 발행 전 편집 화면에 투영하는 문구와 같아야 한다(ScreeningService.DEFAULT_DISCLAIMER). */
	private static final String CONSOLE_DEFAULT =
			"본 설명은 뉴스·공시 등 공개 데이터를 기반으로 자동 생성된 참고 정보이며, "
					+ "특정 종목의 매수·매도를 권유하지 않습니다. 투자 판단과 책임은 투자자 본인에게 있습니다.";

	@DynamicPropertySource
	static void dedicatedContext(DynamicPropertyRegistry registry) {
		registry.add("publication.known-tickers", () -> TICKER);
		// 캐시 TTL 을 테스트 길이보다 충분히 크게 고정한다 — 기본 3s 면 CI 정지·GC 로 요청
		// 사이에 만료될 수 있고, 그러면 두 번째 요청이 DB 미스 경로로 돌아 "캐시를 켠 채
		// 즉시 반영"이라는 검증 대상이 우연히 성립한다(캐시가 문구를 가리는 회귀를 놓친다).
		registry.add("publication.serve-cache-ttl", () -> "60s");
	}

	@Autowired
	private ExplanationService explanationService;

	@Autowired
	private JdbcTemplate jdbc;

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		jdbc.update("DELETE FROM serving_scope");
		// screening_check·screening_rule 이 policy_version 을 참조한다 — 자식부터 지운다.
		jdbc.update("DELETE FROM screening_check");
		jdbc.update("DELETE FROM screening_rule");
		jdbc.update("DELETE FROM policy_version");
		seedPublishedExplanationOnce();
		mvc = MockMvcBuilders
				.standaloneSetup(new ExplanationController(explanationService))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 활성_정책의_문구가_그대로_응답에_실린다() throws Exception {
		// WHY: 이 티켓의 본체 — 콘솔이 발행한 문구가 고객 응답에 도달해야 한다(종전에는 상수가 실렸다).
		publishPolicy(1, "테넌트가 발행한 안내 문구입니다.");
		serve().andExpect(status().isOk())
				.andExpect(jsonPath("$.disclaimer").value("테넌트가 발행한 안내 문구입니다."));
	}

	@Test
	void 문구를_새_버전으로_발행하면_다음_조회부터_반영된다() throws Exception {
		// WHY: 조회 시점 최신값 규칙. 게시분 캐시를 켠 채로도 문구가 즉시 갈려야 한다 —
		// 컴플라이언스 통제는 다음 게시를 기다릴 수 없다.
		publishPolicy(1, "1차 안내 문구.");
		serve().andExpect(jsonPath("$.disclaimer").value("1차 안내 문구."));

		deactivateActivePolicy();
		publishPolicy(2, "2차로 정정된 안내 문구.");

		serve().andExpect(status().isOk())
				.andExpect(jsonPath("$.disclaimer").value("2차로 정정된 안내 문구."));
	}

	@Test
	void 정책_미발행이면_콘솔_기본_문구로_수렴한다() throws Exception {
		// WHY: 활성 0건은 정상 상태다(면책 문구는 테넌트 콘텐츠라 시드로 발행하지 않는다 —
		// policy_version 스키마 COMMENT). 그 구간의 문구가 콘솔 투영과 달라선 안 된다.
		serve().andExpect(status().isOk()).andExpect(jsonPath("$.disclaimer").value(CONSOLE_DEFAULT));
	}

	@Test
	void 종결된_버전의_문구는_쓰이지_않는다() throws Exception {
		// WHY: 활성 술어는 콘솔 writer 의 findActive() 전사다 — 종결분(deactivated_at)을 읽으면
		// 폐기된 문구가 되살아나고, 정책 이력이 쌓일수록 어느 문구가 나갈지 예측 불가가 된다.
		publishPolicy(1, "이미 종결된 옛 문구.");
		deactivateActivePolicy();

		serve().andExpect(status().isOk()).andExpect(jsonPath("$.disclaimer").value(CONSOLE_DEFAULT));
	}

	@Test
	void 활성_문구가_공백이면_기본_문구로_응답하되_로그로_드러낸다() throws Exception {
		// WHY: 콘솔은 공백 발행을 거부하므로(updateDisclaimer 의 INVALID_REQUEST) 이 상태는 콘솔
		// 밖 경로(마이그레이션·직접 SQL)가 만든 무결성 이상이다. 고객에겐 빈 면책 문구 대신 기본
		// 문구를 내보내되(안전), 이상을 조용히 삼키면 콘솔의 활성 정책과 고객 노출이 갈려도
		// 장애로 드러나지 않는다(Rule 12) — 폴백과 fail-loud 를 함께 고정한다.
		publishPolicy(1, "   ");
		Logger serviceLogger = (Logger) LoggerFactory.getLogger(ExplanationService.class);
		ListAppender<ILoggingEvent> captured = new ListAppender<>();
		captured.start();
		serviceLogger.addAppender(captured);
		try {
			serve().andExpect(status().isOk())
					.andExpect(jsonPath("$.disclaimer").value(CONSOLE_DEFAULT));
			assertThat(captured.list).anySatisfy(event -> {
				assertThat(event.getLevel()).isEqualTo(Level.ERROR);
				assertThat(event.getFormattedMessage()).contains("면책 문구가 비어 있다");
			});
		}
		finally {
			serviceLogger.detachAppender(captured);
		}
	}

	@Test
	void 활성화되지_않은_버전의_문구는_쓰이지_않는다() throws Exception {
		// WHY: 활성 술어의 두 조건 중 activated_at 쪽을 고정한다 — deactivated_at IS NULL 만
		// 남겨도 종결분 테스트는 통과하므로, 그 절이 지워지면 아직 활성화되지 않은 버전의 문구가
		// 고객에게 새어 나간다. 스키마는 activated_at·deactivated_at 이 함께 NULL 인 행을 허용한다.
		insertUnactivatedPolicy(1, "아직 활성화되지 않은 문구.");

		serve().andExpect(status().isOk()).andExpect(jsonPath("$.disclaimer").value(CONSOLE_DEFAULT));
	}

	private ResultActions serve() throws Exception {
		return mvc.perform(get("/api/v1/explanations/" + TICKER)
				.param("trade_date", TRADE_DATE.toString()));
	}

	/** 활성 버전 발행 — 활성 1건 부분 유니크(arbiter)라 기존 활성은 먼저 종결돼 있어야 한다. */
	private void publishPolicy(int versionNo, String disclaimer) {
		jdbc.update("""
				INSERT INTO policy_version (version_no, disclaimer_text, activated_at)
				VALUES (?, ?, now())
				""", versionNo, disclaimer);
	}

	/** 활성화 전 버전 — activated_at·deactivated_at 이 함께 NULL 인 행(스키마 CHECK 통과). */
	private void insertUnactivatedPolicy(int versionNo, String disclaimer) {
		jdbc.update("""
				INSERT INTO policy_version (version_no, disclaimer_text)
				VALUES (?, ?)
				""", versionNo, disclaimer);
	}

	private void deactivateActivePolicy() {
		jdbc.update("""
				UPDATE policy_version SET deactivated_at = now()
				WHERE activated_at IS NOT NULL AND deactivated_at IS NULL
				""");
	}

	/**
	 * 게시분은 한 번만 심는다 — publication_id 가 클래스 내내 고정돼야 캐시된 게시분과 원장이
	 * 어긋나지 않는다(클래스 javadoc). 다른 테스트 클래스가 publication 을 비우고 갔을 때만
	 * 다시 심는다.
	 */
	private void seedPublishedExplanationOnce() {
		Integer seeded = jdbc.queryForObject(
				"SELECT count(*) FROM publication WHERE etf_ticker = ? AND trade_date = ?",
				Integer.class, TICKER, TRADE_DATE);
		if (seeded != null && seeded > 0) {
			return;
		}
		// 게시분만 지워진 경우 남아 있을 수 있는 분석 항목을 치우고 다시 심는다(PK 충돌 방지).
		jdbc.update("DELETE FROM analysis_item WHERE explanation_result_id = ?", "a-disc");
		jdbc.update("""
				INSERT INTO analysis_item (explanation_result_id, etf_instrument_id, etf_ticker, etf_name,
				    trade_date, explanation_as_of, explanation_type, summary, confidence_level, status, evidences)
				VALUES (?, ?, ?, ?, ?, now(), 'PRICE_ONLY', ?, 'MEDIUM', 'AUTO_PUBLISHED', NULL)
				""", "a-disc", "instr-a-disc", TICKER, "KODEX 미국S&P500", TRADE_DATE, "요약 a-disc");
		jdbc.update("""
				INSERT INTO publication (analysis_item_id, etf_ticker, trade_date,
				                         explanation_as_of, status)
				VALUES (?, ?, ?, ?::date + time '16:00' at time zone 'Asia/Seoul', 'PUBLISHED')
				""", "a-disc", TICKER, TRADE_DATE, TRADE_DATE);
	}
}
