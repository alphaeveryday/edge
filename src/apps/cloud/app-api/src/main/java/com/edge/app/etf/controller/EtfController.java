package com.edge.app.etf.controller;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.etf.dto.ChartResponse;
import com.edge.app.etf.dto.EtfDetailResponse;
import com.edge.app.etf.dto.EtfSummaryResponse;
import com.edge.app.etf.dto.MoveResponse;
import com.edge.app.etf.service.EtfService;
import com.edge.common.apipayload.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/v1/etfs")
@RequiredArgsConstructor
public class EtfController {
    private final EtfService etfService;

    @GetMapping
    public ApiResponse<List<EtfSummaryResponse>> etfList(@RequestParam(required = false) String q) {
        return ApiResponse.onSuccess(etfService.list(q));
    }

    @GetMapping("/{code}")
    public ApiResponse<EtfSummaryResponse> etfGet(@PathVariable String code, AppPrincipal principal) {
        return ApiResponse.onSuccess(etfService.get(code));
    }

    @GetMapping("/{code}/chart")
    public ApiResponse<ChartResponse> etfChart(@PathVariable String code, AppPrincipal principal,
            @RequestParam(defaultValue = "1M") String range) {
        return ApiResponse.onSuccess(etfService.chart(code, range));
    }

    @GetMapping("/{code}/move")
    public ApiResponse<MoveResponse> etfMove(@PathVariable String code, AppPrincipal principal) {
        return ApiResponse.onSuccess(etfService.move(code));
    }

    @GetMapping("/{code}/detail")
    public ApiResponse<EtfDetailResponse> etfDetail(@PathVariable String code, AppPrincipal principal) {
        return ApiResponse.onSuccess(etfService.detail(code));
    }
}
