package com.edge.tenantsync.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

/**
 * 소스 이벤트 하나 — source_event 경계면 4컬럼 {source_event_id, source_class,
 * event_type_code, event_date}(ALPHA-395 확정, 조립은 ALPHA-718). 온프렘 소비자는
 * screening-worker 출처 수 정책 게이트(SINGLE_SOURCE·min_source_count — 고유
 * source_event_id 수를 센다)다. event_date 는 NULL 허용이지만 계약상 키는 필수라
 * ALWAYS 로 항상 직렬화한다. event_date 는 조립부가 ISO date 문자열로 완성한다
 * (EvidenceItem 의 published_at 과 같은 이유 — 직렬화기 변수 차단).
 */
@JsonInclude(JsonInclude.Include.ALWAYS)
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record SourceEventItem(
		String sourceEventId,
		String sourceClass,
		String eventTypeCode,
		String eventDate
) {
}
