package com.edge.app.community.report.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

public record ReportRequest(@NotBlank @Pattern(regexp = "post|reply") String targetType, @NotBlank String targetId,
        @NotBlank @Pattern(regexp = "spam|abuse|sexual|scam|etc") String reason) {
}
