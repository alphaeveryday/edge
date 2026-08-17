package com.edge.publication.cache;

import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import com.edge.publication.repository.ExplanationStore.PublishedExplanation.Evidence;
import org.junit.jupiter.api.Test;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * L2 값 인코딩 계약 — 캐시가 값을 <b>바꾸면</b> 서빙이 거짓말을 한다.
 * 전 필드(중첩 근거·null 허용 필드·오프셋)가 왕복에서 동일해야 하고, negative 센티널과
 * 오염된 값(깨진 JSON)의 처리가 명확해야 한다(오염 = miss 수렴, 예외 전파 금지).
 */
class RedisExplanationCodecTest {

	private final RedisExplanationCodec codec = new RedisExplanationCodec();

	private static final PublishedExplanation FULL = new PublishedExplanation(
			42L, "069500", "KODEX 200", LocalDate.of(2026, 7, 15),
			"반도체 비중 상위 구성종목의 동반 상승이 반영된 것으로 보이는 변동 요인 후보입니다.",
			"HIGH",
			List.of(new Evidence("news", "삼성전자 실적 발표", "연합뉴스",
							OffsetDateTime.of(2026, 7, 15, 9, 30, 0, 0, ZoneOffset.ofHours(9))),
					new Evidence("disclosure", null, null, null)),
			OffsetDateTime.of(2026, 7, 15, 16, 40, 0, 0, ZoneOffset.ofHours(9)),
			OffsetDateTime.of(2026, 7, 15, 16, 0, 0, 0, ZoneOffset.ofHours(9)),
			OffsetDateTime.of(2026, 7, 15, 15, 30, 0, 0, ZoneOffset.ofHours(9)));

	@Test
	void 전_필드가_왕복에서_보존된다_중첩_근거와_null_필드_포함() {
		String encoded = codec.encode(Optional.of(FULL));

		assertThat(codec.decode(encoded)).contains(Optional.of(FULL));
	}

	@Test
	void 오프셋이_UTC_로_정규화되지_않는다_KST_표시_축() {
		PublishedExplanation decoded = codec.decode(codec.encode(Optional.of(FULL)))
				.orElseThrow().orElseThrow();

		assertThat(decoded.publishedAt().getOffset()).isEqualTo(ZoneOffset.ofHours(9));
	}

	@Test
	void 게시분_없음은_센티널로_왕복한다() {
		String encoded = codec.encode(Optional.empty());

		assertThat(encoded).isEqualTo("NONE");
		assertThat(codec.decode(encoded)).contains(Optional.empty());
	}

	@Test
	void positive_값은_항상_중괄호로_시작해_센티널과_겹치지_않는다() {
		assertThat(codec.encode(Optional.of(FULL))).startsWith("{");
	}

	@Test
	void 깨진_값은_miss_로_수렴한다_예외_전파_금지() {
		assertThat(codec.decode("{\"publicationId\":")).isEmpty();
		assertThat(codec.decode("완전히 JSON 이 아님")).isEmpty();
		assertThat(codec.decode(null)).isEmpty();
		assertThat(codec.decode("")).isEmpty();
	}

	@Test
	void 키는_버전과_거래일을_포함한다() {
		assertThat(RedisExplanationCodec.key("069500", LocalDate.of(2026, 7, 15)))
				.isEqualTo("publication:v1:069500:2026-07-15");
		assertThat(RedisExplanationCodec.key("069500", null))
				.isEqualTo("publication:v1:069500:latest");
	}
}
