package com.edge.publication.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.publication.repository.ExplanationStore;
import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import com.edge.publication.service.ExplanationService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.Duration;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 계약(publication-api.md) 시맨틱을 검증한다 — MTS 위젯이 의존하는 약속이 깨지면 실패해야 한다.
 * 요청은 무헤더가 정상이다(ADR-0053 — 고객 식별·채널 헤더 폐지, 인증 없는 공개 읽기 표면).
 * Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup 을 쓴다.
 * 저장소는 DB(JPA) 구현이 됐으므로 시드 대역으로 대체한다 — 여기서 지키는
 * 것은 HTTP 계약이고, 실 DB 경로는 compose E2E(스키마 제약 포함)가 확인한다.
 */
class ExplanationControllerTest {

	private static final PublishedExplanation SEED = new PublishedExplanation(
			1L, "069500", "KODEX 200", LocalDate.of(2026, 7, 15),
			"반도체 비중 상위 구성종목의 동반 상승이 반영된 것으로 보이는 공개 정보 기반 변동 요인 후보입니다.",
			"MEDIUM",
			List.of(new PublishedExplanation.Evidence("NEWS", "반도체 수출 반등", "demo",
					OffsetDateTime.of(2026, 7, 15, 13, 0, 0, 0, ZoneOffset.ofHours(9)))),
			OffsetDateTime.of(2026, 7, 15, 16, 40, 0, 0, ZoneOffset.ofHours(9)),
			OffsetDateTime.of(2026, 7, 15, 16, 0, 0, 0, ZoneOffset.ofHours(9)),
			OffsetDateTime.of(2026, 7, 15, 10, 30, 0, 0, ZoneOffset.ofHours(9)));

	/** 시드 대역 — 069500 = 게시분 존재, 305720 = 상장이나 설명 없음, 그 외 = 미상장. */
	private static final class SeededStore extends ExplanationStore {
		SeededStore() {
			super(null, Duration.ofSeconds(3));
		}

		@Override
		public Optional<PublishedExplanation> findPublished(String ticker, LocalDate tradeDate) {
			if (!"069500".equals(ticker)) {
				return Optional.empty();
			}
			if (tradeDate != null && !SEED.tradeDate().equals(tradeDate)) {
				return Optional.empty();
			}
			return Optional.of(SEED);
		}
	}

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		// 제공 범위 판정·면책 문구 조회는 실 DB 통합 테스트(ExplanationScopeIntegrationTest·
		// ExplanationDisclaimerIntegrationTest) 소관 — 여기서는 각각 행 부재(전부 제공)와 정책
		// 미발행(기본 문구)으로 두어 기존 HTTP 계약만 검증한다. 상장 판정 대역은 시드와 같은
		// 두 종목만 상장으로 두어 404(미상장) 계약을 살린다 — ticker -> true 면 404 케이스가 죽는다.
		ExplanationService service = new ExplanationService(
				new SeededStore(), Set.of("069500", "305720")::contains,
				(scopeType, scopeKey) -> Optional.empty(), Optional::empty);
		mvc = MockMvcBuilders
				.standaloneSetup(new ExplanationController(service))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 성공_응답은_계약_형상이고_disclaimer가_반드시_포함된다() throws Exception {
		// WHY: 화면(가상 MTS 포함)이 이 필드명으로 렌더링한다. disclaimer 는 규정상 필수 노출 문구.
		// 정책 미발행 구간이라 기본 문구가 실린다 — 이 값은 콘솔이 첫 발행 전 편집 화면에
		// 투영하는 문구와 같아야 한다(ALPHA-772).
		mvc.perform(get("/api/v1/explanations/069500"))
				.andExpect(status().isOk())
				// 공통 응답 포맷(ADR-0054) — 게시분은 result 아래에 실린다.
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.result.publication_id").value("1"))
				.andExpect(jsonPath("$.result.etf.ticker").value("069500"))
				.andExpect(jsonPath("$.result.trade_date").value("2026-07-15"))
				.andExpect(jsonPath("$.result.summary").isNotEmpty())
				.andExpect(jsonPath("$.result.confidence_level").value("MEDIUM"))
				.andExpect(jsonPath("$.result.evidences[0].kind").value("NEWS"))
				.andExpect(jsonPath("$.result.disclaimer").value(
						"본 설명은 뉴스·공시 등 공개 데이터를 기반으로 자동 생성된 참고 정보이며, "
								+ "특정 종목의 매수·매도를 권유하지 않습니다. 투자 판단과 책임은 투자자 본인에게 있습니다."))
				.andExpect(jsonPath("$.result.published_at").isNotEmpty())
				// 스냅샷 기준시각(ADR-0045) — openapi required. 매핑 누락·오배선(published_at
				// 재사용) 회귀를 값 단언으로 거부한다(SEED as_of = 16:00 KST).
				.andExpect(jsonPath("$.result.explanation_as_of").value("2026-07-15T16:00:00+09:00"))
				// 콘텐츠 기준시각(ALPHA-918) — 산문이 말하는 창의 끝을 소비자가 옳은 시각으로 쓸 수 있게 노출
				.andExpect(jsonPath("$.result.content_as_of").value("2026-07-15T10:30:00+09:00"));
	}

	@Test
	void 폐지된_헤더는_값과_무관하게_무시되고_정상_동작한다() throws Exception {
		// WHY: ADR-0053 전환기 — 구 계약(X-Customer-Hash·X-Channel)으로 호출하던 소비자가
		// 헤더를 아직 보내더라도 400 이 아니라 정상 서빙이어야 한다. 구 검증이 거부하던
		// 값(공백 해시·허용값 밖 채널)까지 200 이어야 "읽지 않고 무시" 계약이 고정된다 —
		// 선택적 헤더 검증이 재도입되면 이 테스트가 깨진다.
		mvc.perform(get("/api/v1/explanations/069500")
						.header("X-Customer-Hash", " ").header("X-Channel", "APP"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.publication_id").value("1"));
	}

	@Test
	void 설명_없음은_200에_result_키가_없다() throws Exception {
		// WHY: 설명 없음은 정상 상태(설명 없는 날) — 상장 여부(404)와 다른 질문이고, ADR-0054 로
		// 204 대신 공통 포맷 200 + result 생략이다. 부재는 키 부재여야 한다 — "result": null 명시가
		// 섞이면 소비자 형상 검증이 갈리므로(ALPHA-599 선례) doesNotExist() 가 아니라 키 집합
		// 전수 비교로 거부한다.
		String body = mvc.perform(get("/api/v1/explanations/305720"))
				.andExpect(status().isOk())
				.andReturn().getResponse().getContentAsString();

		JsonNode root = new ObjectMapper().readTree(body);
		assertThat(root.size()).isEqualTo(3);
		assertThat(root.has("isSuccess")).isTrue();
		assertThat(root.get("isSuccess").asBoolean()).isTrue();
		assertThat(root.has("code")).isTrue();
		assertThat(root.has("message")).isTrue();
		assertThat(root.has("result")).isFalse();
	}

	@Test
	void 미상장_코드는_404다() throws Exception {
		mvc.perform(get("/api/v1/explanations/999999"))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("SERV4040"));
	}

	@Test
	void 잘못된_trade_date_형식은_400_공통_포맷이다() throws Exception {
		// WHY: 형식 오류는 연동 버그 신호(fail-loud) — 조용히 무시하고 최신분을 주면 오배선이 숨는다.
		// 파싱은 프레임워크 변환(@DateTimeFormat) 소관이라 코드는 공통 COMMON400 이다(SERV4004 폐지).
		mvc.perform(get("/api/v1/explanations/069500").param("trade_date", "2026/07/15"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("COMMON400"));
	}

	@Test
	void 빈_trade_date는_생략과_같다() throws Exception {
		// WHY: ALPHA-498 수용집합 — 폼·프록시가 만드는 trade_date= 를 400 으로 거부하면 정상
		// 연동이 깨진다. 프레임워크 변환기가 빈 문자열을 null 로 접는 동작에 기대므로, 그 전제가
		// 버전업 등으로 무너지면 이 테스트가 잡는다.
		mvc.perform(get("/api/v1/explanations/069500").param("trade_date", ""))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.publication_id").value("1"));
		// 공백뿐인 값도 생략과 동치다 — openapi anyOf(`^\s*$`)가 전사하는 수용집합의 나머지 절반.
		mvc.perform(get("/api/v1/explanations/069500").param("trade_date", "   "))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.publication_id").value("1"));
	}
}
