package com.edge.app.community.report.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

public record ReportRequest(@NotBlank @Pattern(regexp = "post|comment|reply") String targetType, @NotBlank String targetId,
        @NotBlank @Pattern(regexp = "spam|abuse|sexual|scam|etc") String reason) {
    // 1.1.0 이전 앱의 reply 를 comment 로 통일
    public ReportRequest {
        if ("reply".equals(targetType)) {
            targetType = "comment";
        }
    }
}
