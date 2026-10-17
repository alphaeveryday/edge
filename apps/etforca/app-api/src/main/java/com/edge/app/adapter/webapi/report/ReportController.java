package com.edge.app.adapter.webapi.report;

import com.edge.app.adapter.security.MemberPrincipal;
import com.edge.app.application.report.ReportService;
import com.edge.app.application.report.provided.ReportRequest;
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
