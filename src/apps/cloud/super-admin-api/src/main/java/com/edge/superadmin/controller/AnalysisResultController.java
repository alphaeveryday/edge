package com.edge.superadmin.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.superadmin.dto.AnalysisResultDetailResponse;
import com.edge.superadmin.dto.AnalysisResultPageResponse;
import com.edge.superadmin.service.AnalysisResultService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 새 분석 엔진(v2)의 결과 목록·상세(ALPHA-1333) — 읽기 전용. 옛 설명 엔진 표면
 * {@code /api/v1/analyses}({@link AnalysisController})와 별개다.
 */
@RestController
public class AnalysisResultController {

	private final AnalysisResultService service;

	public AnalysisResultController(AnalysisResultService service) {
		this.service = service;
	}

	/**
	 * @param kind       {@code outlook}(전망) 또는 {@code movement}(가격변동)
	 * @param from       분석 시각의 KST 날짜 하한(포함). 생략하면 오늘
	 * @param to         KST 날짜 상한(포함). 생략하면 {@code from}
	 * @param allSources true 면 {@code data_source} 가 database 가 아닌 행(합성 입력 등)도 싣는다
	 */
	@GetMapping("/api/v1/analysis-results")
	public ApiResponse<AnalysisResultPageResponse> list(@RequestParam(required = false) String kind,
			@RequestParam(required = false) String from,
			@RequestParam(required = false) String to,
			@RequestParam(required = false) String etf,
			@RequestParam(required = false) String status,
			@RequestParam(defaultValue = "false") boolean allSources,
			@RequestParam(defaultValue = "0") int page,
			@RequestParam(defaultValue = "50") int size) {
		return ApiResponse.onSuccess(service.list(kind, from, to, etf, status, allSources, page, size));
	}

	@GetMapping("/api/v1/analysis-results/{kind}/{analysisId}")
	public ApiResponse<AnalysisResultDetailResponse> detail(@PathVariable String kind, @PathVariable String analysisId) {
		return ApiResponse.onSuccess(service.detail(kind, analysisId));
	}
}
