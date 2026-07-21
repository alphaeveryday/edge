package com.edge.intake.envelope;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;

/**
 * 번들 <b>봉투</b>에서 cursor 범위만 뽑는다 — 적재 키(cursor_from)·전진값(cursor_to)에 필요한 최소 판독.
 * 엔트리(delivery_type·explanation_result) 의미 파싱은 하지 않는다 — 그건 Screening 몫(ADR-0036).
 */
public final class BundleEnvelope {

	private static final ObjectMapper MAPPER = JsonMapper.builder().build();

	private BundleEnvelope() {
	}

	public static CursorRange cursorRange(byte[] body) {
		JsonNode root = MAPPER.readTree(body);
		JsonNode from = root.get("cursor_from");
		JsonNode to = root.get("cursor_to");
		if (from == null || to == null || !from.isIntegralNumber() || !to.isIntegralNumber()) {
			// 체크섬은 통과했는데 봉투 계약을 어긴 번들 — 조용히 넘기지 않고 실패시킨다(Rule 12).
			// isIntegralNumber: 비정수(1.9)를 asLong 이 조용히 절삭하지 못하게 정수 cursor 만 허용.
			throw new IllegalStateException("번들 봉투에 정수 cursor_from/cursor_to 가 없다");
		}
		return new CursorRange(from.asLong(), to.asLong());
	}

	public record CursorRange(long from, long to) {
	}
}
