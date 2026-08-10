package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.model.ReviewItem;
import com.edge.tenantconsole.model.ReviewItemDetail;
import org.junit.jupiter.api.Test;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 검수 상세 응답의 와이어 계약(snake_case·NON_NULL)을 검증한다(ALPHA-920). WHY: UI 는
 * `content_as_of` 키로 매핑(`w.content_as_of ?? null`)하고 결측이면 기준시각 행을
 * 생략한다 — camelCase 로 새거나 null 명시로 나가면 값이 조용히 숨거나 "null" 이 그려진다.
 */
class ReviewItemDetailResponseTest {

	private static final ObjectMapper mapper = new ObjectMapper();

	private static ReviewItemDetail sample(OffsetDateTime contentAsOf) {
		ReviewItem item = new ReviewItem("er-1", "069500", "KODEX 200", LocalDate.of(2026, 7, 15),
				"요약", null, "MEDIUM", "REVIEW_REQUIRED",
				OffsetDateTime.parse("2026-07-15T17:00:00+09:00"),
				OffsetDateTime.parse("2026-07-15T16:00:00+09:00"), contentAsOf);
		return new ReviewItemDetail(item, mapper.createArrayNode(), List.of(), List.of(), List.of());
	}

	@Test
	void 콘텐츠_기준시각은_snake_case_로_직렬화된다() {
		JsonNode json = mapper.valueToTree(
				ReviewItemDetailResponse.from(sample(OffsetDateTime.parse("2026-07-15T10:30:00+09:00"))));

		assertThat(json.get("content_as_of").asString()).isEqualTo("2026-07-15T10:30+09:00");
		assertThat(json.has("contentAsOf")).isFalse();
	}

	@Test
	void 콘텐츠_기준시각_결측은_키_생략이다() {
		JsonNode json = mapper.valueToTree(ReviewItemDetailResponse.from(sample(null)));

		assertThat(json.has("content_as_of")).isFalse();
	}
}
