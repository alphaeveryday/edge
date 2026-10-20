package com.edge.app.adapter.webapi.etf;

import com.edge.app.application.etf.provided.ChartResponse;
import com.edge.app.application.etf.provided.EtfDetailResponse;
import com.edge.app.application.etf.provided.EtfFinder;
import com.edge.app.application.etf.provided.EtfSummaryResponse;
import com.edge.app.application.etf.provided.MoveResponse;
import com.edge.app.application.member.provided.AppPrincipal;
import com.edge.app.domain.etf.ChartRange;
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
    private final EtfFinder etfFinder;

    @GetMapping
    public ApiResponse<List<EtfSummaryResponse>> etfList(@RequestParam(required = false) String q) {
        return ApiResponse.onSuccess(etfFinder.list(q));
    }

    @GetMapping("/{code}")
    public ApiResponse<EtfSummaryResponse> etfGet(@PathVariable String code, AppPrincipal principal) {
        return ApiResponse.onSuccess(etfFinder.get(code));
    }

    @GetMapping("/{code}/chart")
    public ApiResponse<ChartResponse> etfChart(@PathVariable String code, AppPrincipal principal,
            @RequestParam(defaultValue = "1M") ChartRange range) {
        return ApiResponse.onSuccess(etfFinder.chart(code, range));
    }

    @GetMapping("/{code}/move")
    public ApiResponse<MoveResponse> etfMove(@PathVariable String code, AppPrincipal principal) {
        return ApiResponse.onSuccess(etfFinder.move(code));
    }

    @GetMapping("/{code}/detail")
    public ApiResponse<EtfDetailResponse> etfDetail(@PathVariable String code, AppPrincipal principal) {
        return ApiResponse.onSuccess(etfFinder.detail(code));
    }
}
