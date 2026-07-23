package com.edge.tenantconsole.controller;

import com.edge.tenantconsole.mock.ExplanationMockStore.Evidence;
import com.edge.tenantconsole.mock.ExplanationMockStore.Explanation;
import com.edge.tenantconsole.mock.ExplanationMockStore.FeedStatus;
import com.edge.tenantconsole.service.ExplanationService;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 가격 변동 설명 표면(ALPHA-513) — tenant-console-ui explanations 도메인 계약
 * (repository.real.ts)과 1:1. 필드명은 UI 타입과 동일한 camelCase 를 쓴다
 * (기존 검수 표면의 snake_case 와 다른 이유 — UI 계약이 SSOT). `final` 은 Java
 * 예약어라 컴포넌트명만 finalText 로 두고 JSON 은 @JsonProperty 로 맞춘다.
 */
@RestController
public class ExplanationController {

	private final ExplanationService explanationService;

	public ExplanationController(ExplanationService explanationService) {
		this.explanationService = explanationService;
	}

	public record EvidenceResponse(String type, String title, String source, String time) {
		static EvidenceResponse from(Evidence e) {
			return new EvidenceResponse(e.type(), e.title(), e.source(), e.time());
		}
	}

	@JsonInclude(JsonInclude.Include.NON_NULL)
	public record ExplanationResponse(
			long id,
			String name,
			String code,
			String market,
			int direction,
			double changePct,
			String status,
			String risk,
			String reviewReason,
			String receivedRelative,
			String receivedAt,
			List<EvidenceResponse> evidence,
			String original,
			@JsonProperty("final") String finalText
	) {
		static ExplanationResponse from(Explanation it) {
			return new ExplanationResponse(it.id(), it.name(), it.code(), it.market(),
					it.direction(), it.changePct(), it.status(), it.risk(), it.reviewReason(),
					it.receivedRelative(), it.receivedAt(),
					it.evidence().stream().map(EvidenceResponse::from).toList(),
					it.original(), it.finalText());
		}
	}

	public record FeedStatusResponse(String state, String lastReceivedRelative, int todayReceived) {
		static FeedStatusResponse from(FeedStatus s) {
			return new FeedStatusResponse(s.state(), s.lastReceivedRelative(), s.todayReceived());
		}
	}

	public record FinalRequest(@JsonProperty("final") String finalText) {
	}

	public record ApproveRequest(@JsonProperty("final") String finalText, String note) {
	}

	public record RejectRequest(String note) {
	}

	@GetMapping("/api/v1/explanations")
	public List<ExplanationResponse> list() {
		return explanationService.list().stream().map(ExplanationResponse::from).toList();
	}

	@GetMapping("/api/v1/explanations/feed-status")
	public FeedStatusResponse feedStatus() {
		return FeedStatusResponse.from(explanationService.feedStatus());
	}

	@PatchMapping("/api/v1/explanations/{id}/final")
	public ResponseEntity<Void> updateFinal(@PathVariable("id") long id,
			@RequestBody(required = false) FinalRequest request) {
		explanationService.updateFinal(id, request == null ? null : request.finalText());
		return ResponseEntity.noContent().build();
	}

	@PostMapping("/api/v1/explanations/{id}/stop")
	public ResponseEntity<Void> stop(@PathVariable("id") long id) {
		explanationService.stop(id);
		return ResponseEntity.noContent().build();
	}

	@PostMapping("/api/v1/explanations/{id}/move-to-review")
	public ResponseEntity<Void> moveToReview(@PathVariable("id") long id) {
		explanationService.moveToReview(id);
		return ResponseEntity.noContent().build();
	}

	@PostMapping("/api/v1/explanations/{id}/approve")
	public ResponseEntity<Void> approve(@PathVariable("id") long id,
			@RequestBody(required = false) ApproveRequest request) {
		explanationService.approve(id, request == null ? null : request.finalText());
		return ResponseEntity.noContent().build();
	}

	@PostMapping("/api/v1/explanations/{id}/reject")
	public ResponseEntity<Void> reject(@PathVariable("id") long id,
			@RequestBody(required = false) RejectRequest request) {
		explanationService.reject(id, request == null ? null : request.note());
		return ResponseEntity.noContent().build();
	}

	@PatchMapping("/api/v1/explanations/{id}/draft")
	public ResponseEntity<Void> saveDraft(@PathVariable("id") long id,
			@RequestBody(required = false) FinalRequest request) {
		explanationService.saveDraft(id, request == null ? null : request.finalText());
		return ResponseEntity.noContent().build();
	}
}
