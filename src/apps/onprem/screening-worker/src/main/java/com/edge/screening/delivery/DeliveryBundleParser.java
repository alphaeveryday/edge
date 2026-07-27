package com.edge.screening.delivery;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 번들 JSON → typed VO 1회 파싱 + 계약 검증(안티커럽션 계층, ADR-0039 §2).
 * 형상 위반(entries 비배열·cursor 결측·evidences/source_events 비배열)은 어느 entry 든
 * 즉시 실패한다 — 조용한 치환·강제는 근거 유실이다(Rule 12). cursor 는 와이어 계약상
 * 전 delivery_type 필수(event-bundle.schema.json required)라 파싱 단계에서 강제한다.
 */
public final class DeliveryBundleParser {

	private final ObjectMapper objectMapper = new ObjectMapper();

	public List<DeliveryEntry> parse(long cursorFrom, byte[] body) {
		JsonNode root = objectMapper.readTree(body);
		// 이중 형상 수용(ADR-0040): 신형은 ApiResponse 봉투(isSuccess 마커로 식별, 번들은 result 아래),
		// 구형은 루트가 곧 번들. isSuccess 는 boolean true 만 수용 — 실패·타입 위반 봉투는 fail-loud
		// 거부한다(intake 게이트의 방어 심화 + 롤링 배포·구버전 저장분 대비, 고객 대면 게시 경로).
		JsonNode isSuccess = root.path("isSuccess");
		JsonNode bundle;
		if (isSuccess.isMissingNode()) {
			bundle = root;
		} else {
			if (!isSuccess.isBoolean() || !isSuccess.asBoolean(false)) {
				throw new IllegalStateException(
						"봉투 isSuccess 가 true(boolean)가 아니다 — 계약 위반 (cursor_from=" + cursorFrom + ")");
			}
			bundle = root.path("result");
		}
		JsonNode entries = bundle.path("entries");
		if (!entries.isArray()) {
			throw new IllegalStateException("번들 body 에 entries 배열이 없다 — 계약 위반 (cursor_from=" + cursorFrom + ")");
		}
		if (entries.isEmpty()) {
			// 와이어 계약은 minItems=1(신규 없음 = 204, 빈 번들 미생성) — 빈 entries 를 성공
			// 처리하면 그 cursor 구간이 점검된 것처럼 영구 마킹돼 계약 위반이 은폐된다(Rule 12).
			throw new IllegalStateException("번들 entries 가 비어 있다 — 계약 위반(minItems=1, cursor_from=" + cursorFrom + ")");
		}
		List<DeliveryEntry> parsed = new ArrayList<>();
		for (JsonNode entry : entries) {
			parsed.add(parseEntry(entry));
		}
		return parsed;
	}

	private static DeliveryEntry parseEntry(JsonNode entry) {
		JsonNode cursor = entry.path("cursor");
		if (!cursor.isIntegralNumber()) {
			throw new IllegalStateException("entry 에 cursor 가 없다 — 감사 추적(source_cursor) 필수, 계약 위반");
		}
		if (!cursor.canConvertToLong()) {
			// int64 범위 밖 정수는 asLong 이 손실 변환한다 — 감사 키가 원본과 어긋난 채 확정된다.
			throw new IllegalStateException("entry.cursor 가 int64 범위를 벗어난다 — 계약 위반");
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
				cursor.asLong(),
				entry.path("delivery_type").asString(null),
				result.isObject() ? parseResult(result) : null,
				strictString(entry, "target_explanation_result_id"),
				strictString(entry, "reason"),
				evidences.isArray() ? evidences.toString() : "[]",
				sourceEvents.isArray() ? countDistinctSources(sourceEvents) : 0);
	}

	/**
	 * 출처 수는 정책 게이트(SINGLE_SOURCE·min_source_count)의 입력이고, 런타임엔 JSON Schema
	 * 검증 계층이 없다 — 식별 불가 요소([null,…]·{})가 건수로 세지면 malformed 근거가 출처
	 * 기준을 통과한다. 출처로 인정하는 최소 조건은 문자열 source_event_id(식별 가능성)이고,
	 * 같은 id 의 중복은 1건으로 센다(와이어 스키마가 uniqueItems 를 안 걸어, 중복이 단일
	 * 출처를 다출처로 부풀리면 자동 게시 임계가 우회된다). 요소 상세 스키마 전체 대조는
	 * 계약 테스트(ALPHA-497) 몫.
	 */
	private static int countDistinctSources(JsonNode sourceEvents) {
		Set<String> sourceEventIds = new HashSet<>();
		for (JsonNode sourceEvent : sourceEvents) {
			JsonNode id = sourceEvent.path("source_event_id");
			if (!sourceEvent.isObject() || !id.isString()) {
				throw new IllegalStateException(
						"entry.source_events 요소에 source_event_id(문자열)가 없다 — 계약 위반");
			}
			sourceEventIds.add(id.asString());
		}
		return sourceEventIds.size();
	}

	/**
	 * 정체성·노출 문면 필드는 존재하면 반드시 문자열 노드다 — Jackson asString 은 숫자·
	 * 불리언을 "123" 으로 강제하는데, 강제된 정체성은 대상 미매치(전이 0행)로 조용히
	 * 소화되고 강제된 문면은 금칙어 게이트가 원본 malformed 를 못 본 채 게시까지 간다
	 * (안티커럽션 계층이 막을 강제 통과). 결측·null 은 각 경로의 기존 시맨틱대로 null.
	 */
	private static String strictString(JsonNode node, String field) {
		JsonNode value = node.path(field);
		if (value.isMissingNode() || value.isNull()) {
			return null;
		}
		if (!value.isString()) {
			throw new IllegalStateException(field + " 가 문자열이 아니다 — 계약 위반");
		}
		return value.asString();
	}

	private static DeliveryEntry.ExplanationResult parseResult(JsonNode result) {
		return new DeliveryEntry.ExplanationResult(
				strictString(result, "explanation_result_id"),
				strictString(result, "etf_instrument_id"),
				strictString(result, "etf_ticker"),
				result.path("etf_name").asString(null),
				LocalDate.parse(result.path("trade_date").asString()),
				OffsetDateTime.parse(result.path("explanation_as_of").asString()),
				result.path("explanation_type").asString(null),
				strictString(result, "summary"),
				strictString(result, "headline"),
				result.path("confidence_level").asString(null),
				result.path("primary_thread_id").asString(null));
	}
}
