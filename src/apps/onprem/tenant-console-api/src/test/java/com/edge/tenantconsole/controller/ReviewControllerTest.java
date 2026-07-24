package com.edge.tenantconsole.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.tenantconsole.entity.AnalysisItemEntity;
import com.edge.tenantconsole.repository.PublicationRepository;
import com.edge.tenantconsole.repository.ReviewItemRepository;
import com.edge.tenantconsole.service.ReviewService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.data.domain.Limit;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 검수 계약(state-machine.md)을 검증한다: 승인 = 전이+재발행이 한 단위, 반려 = 사유 필수,
 * REVIEW_REQUIRED 밖 항목은 409(동시 결정 수렴), grain 선점은 409.
 * Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup 을 쓴다. 리포지토리(JPA)는 좁은
 * 인터페이스라 페이크로 스텁한다 — 실 DB 경로는 Testcontainers 통합 테스트.
 */
class ReviewControllerTest {

	private static final AnalysisItemEntity PENDING = new AnalysisItemEntity(
			"er-rev-1", "069500", "KODEX 200", LocalDate.of(2026, 7, 15),
			"정정 요약", null, "LOW", "REVIEW_REQUIRED", "er-0", "근거 공시 정정",
			OffsetDateTime.of(2026, 7, 15, 17, 0, 0, 0, ZoneOffset.ofHours(9)));

	private static final class StubItems implements ReviewItemRepository {
		AnalysisItemEntity item = PENDING;
		boolean decideResult = true;
		final List<String> decisions = new ArrayList<>();

		@Override
		public List<AnalysisItemEntity> findByStatusOrderByReceivedAtAsc(String status, Limit limit) {
			return item != null && item.getStatus().equals(status) ? List.of(item) : List.of();
		}

		@Override
		public Optional<AnalysisItemEntity> findById(String id) {
			return Optional.ofNullable(
					item != null && item.getExplanationResultId().equals(id) ? item : null);
		}

		@Override
		public int decide(String id, String decidedStatus) {
			decisions.add(id + ":" + decidedStatus);
			return decideResult ? 1 : 0;
		}
	}

	private static final class StubPublications implements PublicationRepository {
		boolean publishResult = true;
		final List<String> published = new ArrayList<>();

		@Override
		public int publish(String analysisItemId, String etfTicker, LocalDate tradeDate) {
			published.add(analysisItemId);
			return publishResult ? 1 : 0;
		}
	}

	private StubItems items;
	private StubPublications publications;
	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		items = new StubItems();
		publications = new StubPublications();
		mvc = MockMvcBuilders
				.standaloneSetup(new ReviewController(new ReviewService(items, publications)))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 검수_대기_목록은_계약_형상이다() throws Exception {
		// WHY: 콘솔 화면(review 도메인 repository.real)이 이 필드명으로 렌더링한다.
		mvc.perform(get("/api/v1/review/items"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result[0].explanation_result_id").value("er-rev-1"))
				.andExpect(jsonPath("$.result[0].status").value("REVIEW_REQUIRED"))
				.andExpect(jsonPath("$.result[0].supersedes_item_id").value("er-0"))
				.andExpect(jsonPath("$.result[0].correction_reason").value("근거 공시 정정"));
	}

	@Test
	void 승인은_APPROVED_전이와_재발행을_함께_수행한다() throws Exception {
		// WHY: 검수 승인 후에만 재발행(state-machine.md) — 전이만 되고 게시가 빠지면
		// 고객에겐 여전히 미노출이라 승인의 의미가 없다.
		mvc.perform(post("/api/v1/review/items/er-rev-1/approve"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));

		assertThat(items.decisions).containsExactly("er-rev-1:APPROVED");
		assertThat(publications.published).containsExactly("er-rev-1");
	}

	@Test
	void 검수_대기가_아니면_승인은_409다() throws Exception {
		// WHY: 동시 검수·재클릭이 이중 결정으로 이어지면 안 된다 — 전이 0행 = 충돌 수렴.
		items.decideResult = false;
		mvc.perform(post("/api/v1/review/items/er-rev-1/approve"))
				.andExpect(status().isConflict())
				.andExpect(jsonPath("$.code").value("CNSL4090"));
		assertThat(publications.published).isEmpty();
	}

	@Test
	void grain_선점_시_승인은_409다() throws Exception {
		publications.publishResult = false;
		mvc.perform(post("/api/v1/review/items/er-rev-1/approve"))
				.andExpect(status().isConflict())
				.andExpect(jsonPath("$.code").value("CNSL4091"));
	}

	@Test
	void 반려는_사유가_필수다() throws Exception {
		// WHY: 반려 사유는 감사 재현의 최소 단서 — 빈 사유가 계약을 통과하면 안 된다.
		mvc.perform(post("/api/v1/review/items/er-rev-1/reject")
						.contentType(MediaType.APPLICATION_JSON).content("{\"reason\":\"  \"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("CNSL4001"));

		mvc.perform(post("/api/v1/review/items/er-rev-1/reject")
						.contentType(MediaType.APPLICATION_JSON).content("{\"reason\":\"근거 불충분\"}"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));
		assertThat(items.decisions).containsExactly("er-rev-1:REJECTED");
	}

	@Test
	void 미지의_항목은_404_잘못된_상태_필터는_400이다() throws Exception {
		mvc.perform(post("/api/v1/review/items/no-such/approve"))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("CNSL4040"));
		mvc.perform(get("/api/v1/review/items").param("status", "WHATEVER"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("CNSL4002"));
	}
}
