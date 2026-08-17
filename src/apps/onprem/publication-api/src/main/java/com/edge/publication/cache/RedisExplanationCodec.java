package com.edge.publication.cache;

import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.cfg.DateTimeFeature;
import tools.jackson.databind.json.JsonMapper;

import java.time.LocalDate;
import java.util.Optional;

/**
 * L2(Redis) 값 인코딩 — 다중 인스턴스 캐시 로컬 실험(LOCAL-4/5) 전용.
 * 캐시 값은 앱 간 계약이므로 키에 스키마 버전(v1)을 박아, 형상이 바뀌면 옛 값을 읽지 않고
 * 새 키 공간으로 갈아탄다(마이그레이션 없는 무효화).
 */
public class RedisExplanationCodec {

	private static final Logger log = LoggerFactory.getLogger(RedisExplanationCodec.class);

	/** "게시분 없음"(negative) 센티널 — positive 값은 항상 '{' 로 시작해 모호하지 않다. */
	private static final String NONE = "NONE";

	// 오프셋 보존: 표시 규칙이 KST(+09:00) 기준이라 UTC 로 정규화되면 왕복이 값을 바꾼다.
	private final ObjectMapper mapper = JsonMapper.builder()
			.disable(DateTimeFeature.ADJUST_DATES_TO_CONTEXT_TIME_ZONE)
			.build();

	public String encode(Optional<PublishedExplanation> value) {
		return value.map(mapper::writeValueAsString).orElse(NONE);
	}

	/**
	 * 바깥 Optional 이 비면 "쓸 수 없는 값"(부재·깨진 JSON) = miss 수렴이다 —
	 * 캐시 값의 오염이 서빙 오류가 되면 안 된다(캐시는 언제나 버려도 되는 사본).
	 */
	public Optional<Optional<PublishedExplanation>> decode(String raw) {
		if (raw == null || raw.isBlank()) {
			return Optional.empty();
		}
		if (NONE.equals(raw)) {
			return Optional.of(Optional.empty());
		}
		try {
			return Optional.of(Optional.of(mapper.readValue(raw, PublishedExplanation.class)));
		}
		catch (RuntimeException e) {
			log.error("L2 캐시 값 디코드 실패 — miss 로 수렴한다: {}", e.toString());
			return Optional.empty();
		}
	}

	public static String key(String ticker, LocalDate tradeDate) {
		return "publication:v1:" + ticker + ":" + (tradeDate == null ? "latest" : tradeDate);
	}
}
