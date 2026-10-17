package com.edge.app.adapter.webapi.analysis;

import com.edge.app.application.analysis.AnalysisService;
import com.edge.app.application.analysis.provided.DailyAnalysisResponse;
import com.edge.app.application.analysis.provided.FactorPageResponse;
import com.edge.app.application.analysis.provided.MetricPageResponse;
import com.edge.app.domain.analysis.Axis;
import com.edge.common.apipayload.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;

@RestController
@RequestMapping("/api/v1/etfs/{code}/analysis")
@RequiredArgsConstructor
public class AnalysisController {
    private final AnalysisService analysisService;

    @GetMapping
    public ApiResponse<DailyAnalysisResponse> analysisDaily(@PathVariable String code,
            @RequestParam(required = false) LocalDate date, @RequestParam(required = false) LocalDate week) {
        return ApiResponse.onSuccess(analysisService.daily(code, date, week));
    }

    @GetMapping("/factors/{axis}")
    public ApiResponse<FactorPageResponse> analysisFactor(@PathVariable String code, @PathVariable Axis axis) {
        return ApiResponse.onSuccess(analysisService.factor(code, axis));
    }

    @GetMapping("/metrics/{axis}")
    public ApiResponse<MetricPageResponse> analysisMetric(@PathVariable String code, @PathVariable Axis axis) {
        return ApiResponse.onSuccess(analysisService.metric(code, axis));
    }
}
