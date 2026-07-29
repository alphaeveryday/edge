package com.edge.tenantconsole.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.entity.ServingScopeEntity;
import com.edge.tenantconsole.repository.ScopeInstrumentRepository;
import com.edge.tenantconsole.repository.ServingScopeRepository;
import com.edge.tenantconsole.service.ScopeService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * UI 계약(tenant-console-ui scope 도메인)과 실 DB 전환(ALPHA-606)의 계약을 검증한다:
 * 종목 유니버스는 analysis_item 조회분이고, 제공 여부는 serving_scope 옵트아웃 토글
 * (행 부재 = 기본 제공)이며, 토글은 감사 주체(updated_by)를 남긴다. 시장은 국내 상장
 * ETF 한정(ADR-0024)이라 KRX 하나이고 serving_scope 엔 MIC(XKRX)로 저장된다. 시장
 * 카드의 종목 수는 유니버스 크기에서 파생돼 별도 카운터 드리프트가 없다. Boot 4 는
 * @WebMvcTest 슬라이스가 없어 standaloneSetup.
 */
class ScopeControllerTest {

	private static final SessionMember ADMIN =
			new SessionMember(1L, "admin@demo.edge.local", "데모 관리자", "TENANT_ADMIN");

	/** in-memory serving_scope 대역 — (scope_type, scope_key) 유니크 upsert 를 흉내낸다. */
	private static final class FakeScopes implements ServingScopeRepository {
		final Map<String, ServingScopeEntity> byKey = new LinkedHashMap<>();
		private long nextId = 1;
		final List<Long> capturedUpdatedBy = new ArrayList<>();

		private static String key(String type, String scopeKey) {
			return type + '|' + scopeKey;
		}

		@Override
		public List<ServingScopeEntity> findByScopeType(String scopeType) {
			return byKey.values().stream().filter(s -> s.getScopeType().equals(scopeType)).toList();
		}

		@Override
		public Optional<ServingScopeEntity> findByScopeTypeAndScopeKey(String scopeType, String scopeKey) {
			return Optional.ofNullable(byKey.get(key(scopeType, scopeKey)));
		}

		@Override
		public void toggle(String scopeType, String scopeKey, long updatedBy) {
			capturedUpdatedBy.add(updatedBy);
			byKey.compute(key(scopeType, scopeKey), (k, existing) -> new ServingScopeEntity(
					existing == null ? nextId++ : existing.getServingScopeId(),
					scopeType, scopeKey, existing != null && !existing.isEnabled()));
		}
	}

	/** in-memory 유니버스 대역 — analysis_item distinct 종목을 흉내낸다. */
	private static final class FakeInstruments implements ScopeInstrumentRepository {
		private record Row(String code, String name) implements ScopeInstrumentRow {
			@Override
			public String getCode() {
				return code;
			}

			@Override
			public String getName() {
				return name;
			}
		}

		private final List<Row> universe = List.of(
				new Row("069500", "KODEX 200"),
				new Row("133690", "TIGER 미국나스닥100"),
				new Row("305720", "KODEX 2차전지산업"));

		@Override
		public List<ScopeInstrumentRow> findUniverse() {
			return List.copyOf(universe);
		}

		@Override
		public boolean existsInUniverse(String etfTicker) {
			return universe.stream().anyMatch(r -> r.code().equals(etfTicker));
		}
	}

	private FakeScopes scopes;
	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		scopes = new FakeScopes();
		mvc = MockMvcBuilders
				.standaloneSetup(new ScopeController(new ScopeService(scopes, new FakeInstruments())))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	private MockHttpSession session() {
		MockHttpSession session = new MockHttpSession();
		session.setAttribute(SessionMember.SESSION_KEY, ADMIN);
		return session;
	}

	@Test
	void 시장은_KRX_하나이고_종목_수는_유니버스에서_집계한다() throws Exception {
		// WHY: MVP 커버리지는 국내 상장 ETF 한정(ADR-0024) — 시장은 KRX 하나이고, 카드의
		// 종목 수는 별도 카운터가 아니라 유니버스 크기라 화면 불일치가 없다.
		mvc.perform(get("/api/v1/scope/markets"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.length()").value(1))
				.andExpect(jsonPath("$.result[0].market").value("KRX"))
				.andExpect(jsonPath("$.result[0].enabled").value(true))   // 옵트아웃 기본 제공
				.andExpect(jsonPath("$.result[0].stockCount").value(3));
	}

	@Test
	void 시장_토글은_MIC로_저장하고_제공_여부를_뒤집는다() throws Exception {
		mvc.perform(post("/api/v1/scope/markets/KRX/toggle").session(session()))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));

		// serving_scope 엔 MIC(XKRX)로 저장된다(ADR-0027).
		assertThat(scopes.findByScopeTypeAndScopeKey("MARKET", "XKRX")).isPresent();
		mvc.perform(get("/api/v1/scope/markets"))
				.andExpect(jsonPath("$.result[0].enabled").value(false));
	}

	@Test
	void 없는_시장_토글은_404다() throws Exception {
		// WHY: 시장 어휘는 KRX 뿐(ADR-0024) — 임의 경로 값이 새 시장을 만들면 안 된다.
		mvc.perform(post("/api/v1/scope/markets/NYSE/toggle").session(session()))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("CNSL4043"));
	}

	@Test
	void 종목_목록은_유니버스에_옵트아웃_토글을_덧씌운다() throws Exception {
		// WHY: 유니버스는 analysis_item(실 수신 ETF)이고 제공 여부는 serving_scope
		// 옵트아웃이다 — 토글 이력이 없는 종목은 기본 제공(true), 제외한 종목만 false.
		mvc.perform(post("/api/v1/scope/stocks/133690/toggle").session(session()))
				.andExpect(status().isOk());

		mvc.perform(get("/api/v1/scope/stocks"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.length()").value(3))
				.andExpect(jsonPath("$.result[0].code").value("069500"))
				.andExpect(jsonPath("$.result[0].name").value("KODEX 200"))
				.andExpect(jsonPath("$.result[0].market").value("KRX"))
				.andExpect(jsonPath("$.result[0].enabled").value(true))    // 토글 이력 없음
				.andExpect(jsonPath("$.result[1].code").value("133690"))
				.andExpect(jsonPath("$.result[1].enabled").value(false));  // 제외됨
	}

	@Test
	void 없는_종목_토글은_404다() throws Exception {
		// WHY: 유니버스에 없는 티커 토글은 아무 행도 만들지 않고 404 로 드러난다.
		mvc.perform(post("/api/v1/scope/stocks/000000/toggle").session(session()))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("CNSL4043"));
		assertThat(scopes.findByScopeType("INSTRUMENT")).isEmpty();
	}

	@Test
	void 토글은_감사_주체를_serving_scope에_남긴다() throws Exception {
		// WHY: 제공 범위 변경은 이해상충 통제(ADR-0023) — 누가 바꿨는지(updated_by)가
		// 없으면 통제 감사가 끊긴다. serving_scope.updated_by 가 그 기록점이다.
		mvc.perform(post("/api/v1/scope/stocks/069500/toggle").session(session()))
				.andExpect(status().isOk());
		assertThat(scopes.capturedUpdatedBy).containsExactly(1L);
	}
}
