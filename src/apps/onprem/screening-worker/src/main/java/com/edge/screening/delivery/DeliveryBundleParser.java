package com.edge.screening.delivery;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;

/**
 * 번들 JSON → typed VO 1회 파싱 + 계약 검증(안티커럽션 계층, ADR-0039 §2).
 * 형상 위반(entries 비배열·cursor 결측·evidences/source_events 비배열)은 어느 entry 든
 * 즉시 실패한다 — 조용한 치환·강제는 근거 유실이다(Rule 12). cursor 는 와이어 계약상
 * 전 delivery_type 필수(event-bundle.schema.json required)라 파싱 단계에서 강제한다.
 */
public final class DeliveryBundleParser {

	private final ObjectMapper objectMapper = new ObjectMapper();

	public List<DeliveryEntry> parse(long cursorFrom, byte[] body) {
		JsonNode entries = objectMapper.readTree(body).path("entries");
		if (!entries.isArray()) {
			throw new IllegalStateException("번들 body 에 entries 배열이 없다 — 계약 위반 (cursor_from=" + cursorFrom + ")");
		}
		List<DeliveryEntry> parsed = new ArrayList<>();
		for (JsonNode entry : entries) {
			parsed.add(parseEntry(entry));
		}
		return parsed;
	}

	private static DeliveryEntry parseEntry(JsonNode entry) {
		if (!entry.path("cursor").isIntegralNumber()) {
			throw new IllegalStateException("entry 에 cursor 가 없다 — 감사 추적(source_cursor) 필수, 계약 위반");
		}
		JsonNode evidences = entry.path("evidences");
		if (!evidences.isMissingNode() && !evidences.isNull() && !evidences.isArray()) {
			throw new IllegalStateException("entry.evidences 가 배열이 아니다 — 근거 유실 위험, 계약 위반");
		}
		JsonNode sourceEvents = entry.path("source_events");
		if (!sourceEvents.isMissingNode() && !sourceEvents.isNull() && !sourceEvents.isArray()) {
			throw new IllegalStateException("entry.source_events 가 배열이 아니다 — 출처 수 판정 불가, 계약 위반");
		}
		JsonNode result = entry.path("explanation_result");
		return new DeliveryEntry(
				entry.path("cursor").asLong(),
				entry.path("delivery_type").asString(null),
				result.isObject() ? parseResult(result) : null,
				entry.path("target_explanation_result_id").asString(null),
				entry.path("reason").asString(null),
				evidences.isArray() ? evidences.toString() : "[]",
				sourceEvents.isArray() ? sourceEvents.size() : 0);
	}

	private static DeliveryEntry.ExplanationResult parseResult(JsonNode result) {
		return new DeliveryEntry.ExplanationResult(
				result.path("explanation_result_id").asString(null),
				result.path("etf_instrument_id").asString(null),
				result.path("etf_ticker").asString(null),
				result.path("etf_name").asString(null),
				LocalDate.parse(result.path("trade_date").asString()),
				OffsetDateTime.parse(result.path("explanation_as_of").asString()),
				result.path("explanation_type").asString(null),
				result.path("summary").asString(null),
				result.path("headline").asString(null),
				result.path("confidence_level").asString(null),
				result.path("primary_thread_id").asString(null));
	}
}
