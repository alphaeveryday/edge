package com.edge.tenantconsole.dto;

import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

/**
 * 수정 승인 요청(ALPHA-437) — edited_summary 필수(누락·공백은 400: 편집 의도가 일반
 * 승인으로 강등되지 않게). headline 은 서빙 노출 경로(published_summary)가 없어 받지
 * 않는다 — 노출은 안 바뀌는데 기록만 EDITED 가 되는 불일치를 만들지 않는다. snake_case.
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record ReviewEditedApproveRequest(String editedSummary, String note) {
}
