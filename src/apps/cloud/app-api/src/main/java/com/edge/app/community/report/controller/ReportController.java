package com.edge.app.community.report.controller;

import com.edge.app.common.auth.MemberPrincipal;
import com.edge.app.community.report.dto.ReportRequest;
import com.edge.app.community.report.service.ReportService;
import com.edge.common.apipayload.ApiResponse;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/reports")
@RequiredArgsConstructor
public class ReportController {
    private final ReportService reportService;

    @PostMapping
    public ApiResponse<Void> communityReport(MemberPrincipal principal, @RequestBody @Valid ReportRequest request) {
        reportService.report(principal.memberId(), request);
        return ApiResponse.onSuccess(null);
    }
}
