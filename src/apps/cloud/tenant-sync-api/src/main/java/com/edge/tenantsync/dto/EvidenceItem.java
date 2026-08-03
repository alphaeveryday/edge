package com.edge.tenantsync.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

/**
 * 근거 문서 하나 — document 경계면의 flat 화 {kind, title, source, published_at}
 * (ALPHA-395 확정, 조립은 ALPHA-718). kind ← document_type, source ← source_code.
 * title·published_at 은 NULL 허용이지만 계약상 키 자체는 필수(required)라 NON_NULL 생략
 * 없이 항상 직렬화한다(ALWAYS 명시 — 전역 설정이 바뀌어도 계약이 깨지지 않게).
 * published_at 은 조립부가 ISO-8601(UTC Instant) 문자열로 완성해 싣는다 — java.time
 * 직렬화기의 초 생략(:00 탈락)이 계약 format(date-time) 위반을 만드는 것을 차단한다.
 */
@JsonInclude(JsonInclude.Include.ALWAYS)
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record EvidenceItem(
		String kind,
		String title,
		String source,
		String publishedAt
) {
}
