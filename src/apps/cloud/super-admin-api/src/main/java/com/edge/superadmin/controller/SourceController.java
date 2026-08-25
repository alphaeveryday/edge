package com.edge.superadmin.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.superadmin.dto.HoldingsImpactResponse;
import com.edge.superadmin.dto.MinuteStatusResponse;
import com.edge.superadmin.dto.NewsLineageResponse;
import com.edge.superadmin.dto.SourceGridResponse;
import com.edge.superadmin.dto.SourceOverviewResponse;
import com.edge.superadmin.dto.SourceReportResponse;
import com.edge.superadmin.service.SourceService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 데이터 소스 수집 상태 표면 — super-admin-ui sources 도메인 계약과 1:1(ALPHA-515 → 514).
 * 필드명은 UI 타입과 동일한 camelCase. 응답 조립은 서비스가 하고 여기선 감싸기만 한다.
 */
@RestController
public class SourceController {

	private final SourceService sourceService;

	public SourceController(SourceService sourceService) {
		this.sourceService = sourceService;
	}

	/**
	 * @param runKey 볼 런의 슬롯 키. <b>선택</b>이라 없으면 지금까지처럼 최신 런이다 — 드릴다운은
	 *               새 엔드포인트가 아니라 이 화면을 <b>주소 지정 가능</b>하게 만든 것뿐이다.
	 */
	@GetMapping("/api/v1/sources/report")
	public ApiResponse<SourceReportResponse> report(
			@RequestParam(required = false) String runKey) {
		return ApiResponse.onSuccess(sourceService.report(runKey));
	}

	/** 실행 격자(슬롯×작업) — 최근 {@code days}일의 런 전부(ALPHA-594). 범위 검증은 서비스가 한다. */
	@GetMapping("/api/v1/sources/grid")
	public ApiResponse<SourceGridResponse> grid(@RequestParam(defaultValue = "30") int days) {
		return ApiResponse.onSuccess(sourceService.grid(days));
	}

	/** Run Overview — 레인별 최신 런의 운영 요약(ALPHA-683). 판정은 서비스 한 곳에서 한다. */
	@GetMapping("/api/v1/sources/overview")
	public ApiResponse<SourceOverviewResponse> overview() {
		return ApiResponse.onSuccess(sourceService.overview());
	}

	/** holdings 결손 영향(ALPHA-686) — 누락 ETF 와 기준일 분석 지목. */
	@GetMapping("/api/v1/sources/impact/holdings")
	public ApiResponse<HoldingsImpactResponse> holdingsImpact(
			@RequestParam(required = false) String runKey) {
		return ApiResponse.onSuccess(sourceService.holdingsImpact(runKey));
	}

	/** 장중 1분 파이프라인 요약(ALPHA-651) — 세션·창 집계·문제 창 목록. 검증은 서비스가 한다. */
	@GetMapping("/api/v1/sources/minute")
	public ApiResponse<MinuteStatusResponse> minuteStatus(
			@RequestParam(required = false) String date) {
		return ApiResponse.onSuccess(sourceService.minuteStatus(date));
	}

	/** 뉴스 계보(ALPHA-685·697) — 집계·근거 목록·1분 추출 요약. 검증은 서비스가 한다. */
	@GetMapping("/api/v1/sources/lineage/news")
	public ApiResponse<NewsLineageResponse> newsLineage(
			@RequestParam(required = false) String date,
			@RequestParam(defaultValue = "50") int limit,
			@RequestParam(required = false) String stage) {
		return ApiResponse.onSuccess(sourceService.newsLineage(date, limit, stage));
	}
}
