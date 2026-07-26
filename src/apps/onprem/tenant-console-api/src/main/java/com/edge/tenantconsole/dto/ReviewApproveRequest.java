package com.edge.tenantconsole.dto;

import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

/**
 * 승인 요청(ALPHA-437) — 바디 없이 보내면 일반 승인, 편집 필드가 있으면 수정 승인
 * (EDITED_APPROVED·수정 문구로 게시). 필드는 검수 표면 규약대로 snake_case.
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record ReviewApproveRequest(String editedSummary, String editedHeadline, String note) {
}
