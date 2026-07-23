package com.edge.superadmin.controller;

import com.edge.superadmin.mock.AnalysisMockStore.Analysis;
import com.edge.superadmin.service.AnalysisService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 가격 변동 분석 표면(ALPHA-515) — super-admin-ui analyses 도메인 계약과 1:1.
 * 필드명은 UI 타입과 동일한 camelCase. 정정/무효화 사유 필수·감사 레코드
 * (super-admin-console.md)는 DB 연동 시 UI 계약과 함께 편입한다.
 */
@RestController
public class AnalysisController {

	private final AnalysisService analysisService;

	public AnalysisController(AnalysisService analysisService) {
		this.analysisService = analysisService;
	}

	public record CorrectRequest(String result) {
	}

	@GetMapping("/api/v1/analyses")
	public List<Analysis> list() {
		return analysisService.list();
	}

	@PatchMapping("/api/v1/analyses/{id}/result")
	public ResponseEntity<Void> correct(@PathVariable String id,
			@RequestBody(required = false) CorrectRequest request) {
		analysisService.correct(id, request == null ? null : request.result());
		return ResponseEntity.noContent().build();
	}

	@PostMapping("/api/v1/analyses/{id}/exclude")
	public ResponseEntity<Void> exclude(@PathVariable String id) {
		analysisService.exclude(id);
		return ResponseEntity.noContent().build();
	}

	@PostMapping("/api/v1/analyses/{id}/restore")
	public ResponseEntity<Void> restore(@PathVariable String id) {
		analysisService.restore(id);
		return ResponseEntity.noContent().build();
	}
}
