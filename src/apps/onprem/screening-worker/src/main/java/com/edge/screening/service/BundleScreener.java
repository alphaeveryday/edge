package com.edge.screening.service;

import com.edge.screening.repository.AnalysisItemRepository;
import com.edge.screening.repository.PendingBundleRepository;
import com.edge.screening.repository.PublicationRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;
import java.time.OffsetDateTime;

/**
 * 번들 1건의 점검 트랜잭션 — 엔트리 상태 분기 + screened_at 마킹을 한 단위로 commit.
 * 상태 분기(state-machine.md 확정 결정):
 * - NEW: walking skeleton 정책 = 무조건 통과 → AUTO_PUBLISHED + 자동 게시(grain 선점 시 skip)
 * - CORRECTION: 구 리비전 CORRECTED(terminal)·게시분 UNPUBLISHED, 정정분은 새 리비전으로
 *   REVIEW_REQUIRED — 자동 노출 경로 없음(검수 승인은 tenant-console-api)
 * - INVALIDATION: item·게시분 INVALIDATED — 즉시 비노출(검수 불요, 보수적 방향)
 * 형상 위반(미지의 delivery_type·본체 결측)은 마킹 없이 실패 — 오류가 조용히 소화되지 않는다.
 */
@Service
public class BundleScreener {

	private static final Logger log = LoggerFactory.getLogger(BundleScreener.class);

	private final PendingBundleRepository pendingBundleRepository;
	private final AnalysisItemRepository analysisItemRepository;
	private final PublicationRepository publicationRepository;
	private final ObjectMapper objectMapper = new ObjectMapper();

	public BundleScreener(PendingBundleRepository pendingBundleRepository,
			AnalysisItemRepository analysisItemRepository,
			PublicationRepository publicationRepository) {
		this.pendingBundleRepository = pendingBundleRepository;
		this.analysisItemRepository = analysisItemRepository;
		this.publicationRepository = publicationRepository;
	}

	@Transactional
	public void screen(long cursorFrom, byte[] body) {
		JsonNode entries = objectMapper.readTree(body).path("entries");
		if (!entries.isArray()) {
			throw new IllegalStateException("번들 body 에 entries 배열이 없다 — 계약 위반 (cursor_from=" + cursorFrom + ")");
		}
		for (JsonNode entry : entries) {
			String deliveryType = entry.path("delivery_type").asString(null);
			switch (deliveryType) {
				case "NEW" -> screenNew(entry);
				case "CORRECTION" -> screenCorrection(entry);
				case "INVALIDATION" -> screenInvalidation(entry);
				case null, default -> throw new IllegalStateException(
						"미지의 delivery_type=" + deliveryType + " (cursor=" + entry.path("cursor").asLong() + ")");
			}
		}
		pendingBundleRepository.markScreened(cursorFrom);
		log.info("bundle screened cursor_from={} entries={}", cursorFrom, entries.size());
	}

	private void screenNew(JsonNode entry) {
		JsonNode result = requiredResult(entry);
		String id = result.path("explanation_result_id").asString(null);
		String ticker = result.path("etf_ticker").asString(null);
		LocalDate tradeDate = LocalDate.parse(result.path("trade_date").asString());

		upsertItem(entry, result, null, null, "AUTO_PUBLISHED");
		if (ticker == null) {
			// 경계면 계약상 ticker 는 공급되지만, 결측이면 게시(서빙 키)가 불가능하다 —
			// 수신은 보존하되 노출은 하지 않는다(fail-safe 방향).
			log.error("NEW entry 에 etf_ticker 결측 — 게시 불가, 항목만 보존 (id={})", id);
			return;
		}
		boolean published = publicationRepository.publish(id, ticker, tradeDate) > 0;
		log.info("NEW screened id={} auto_published={} (grain 선점 시 skip)", id, published);
	}

	private void screenCorrection(JsonNode entry) {
		JsonNode result = requiredResult(entry);
		String target = entry.path("target_explanation_result_id").asString(null);
		String reason = entry.path("reason").asString(null);
		if (target == null) {
			throw new IllegalStateException("CORRECTION 에 target_explanation_result_id 가 없다 — 계약 위반");
		}
		// 구 리비전 종결 + 게시분 즉시 비노출 — 대상 미수신(gap)은 로그로 표면화(gap 감지는 후속)
		int corrected = analysisItemRepository.transition(target, "CORRECTED");
		int unpublished = publicationRepository.transitionByItem(target, "UNPUBLISHED");
		if (corrected == 0) {
			log.warn("CORRECTION 대상 미수신 target={} — gap 가능성(감지는 후속), 정정분은 정상 진입", target);
		}
		// 정정분 = 새 리비전, 재검수 대상 — 자동 노출 경로 없음(state-machine.md 확정)
		upsertItem(entry, result, target, reason, "REVIEW_REQUIRED");
		log.info("CORRECTION screened target={} corrected={} unpublished={} revision={}",
				target, corrected, unpublished, result.path("explanation_result_id").asString(null));
	}

	private void screenInvalidation(JsonNode entry) {
		String target = entry.path("target_explanation_result_id").asString(null);
		if (target == null) {
			throw new IllegalStateException("INVALIDATION 에 target_explanation_result_id 가 없다 — 계약 위반");
		}
		int invalidated = analysisItemRepository.transition(target, "INVALIDATED");
		int removed = publicationRepository.transitionByItem(target, "INVALIDATED");
		if (invalidated == 0) {
			log.warn("INVALIDATION 대상 미수신 target={} — gap 가능성(감지는 후속)", target);
		}
		log.info("INVALIDATION screened target={} removed_publications={}", target, removed);
	}

	private void upsertItem(JsonNode entry, JsonNode result, String supersedesItemId,
			String correctionReason, String status) {
		if (!entry.path("cursor").isIntegralNumber()) {
			throw new IllegalStateException("entry 에 cursor 가 없다 — 감사 추적(source_cursor) 필수, 계약 위반");
		}
		JsonNode evidences = entry.path("evidences");
		if (!evidences.isMissingNode() && !evidences.isNull() && !evidences.isArray()) {
			throw new IllegalStateException("entry.evidences 가 배열이 아니다 — 근거 유실 위험, 계약 위반");
		}
		analysisItemRepository.upsert(
				result.path("explanation_result_id").asString(null),
				result.path("etf_instrument_id").asString(null),
				result.path("etf_ticker").asString(null),
				result.path("etf_name").asString(null),
				LocalDate.parse(result.path("trade_date").asString()),
				OffsetDateTime.parse(result.path("explanation_as_of").asString()),
				result.path("explanation_type").asString(null),
				result.path("summary").asString(null),
				result.path("headline").asString(null),
				result.path("confidence_level").asString(null),
				result.path("primary_thread_id").asString(null),
				evidences.isArray() ? evidences.toString() : "[]",
				supersedesItemId,
				correctionReason,
				entry.path("cursor").asLong(),
				status);
	}

	private static JsonNode requiredResult(JsonNode entry) {
		JsonNode result = entry.path("explanation_result");
		if (!result.isObject()) {
			throw new IllegalStateException(
					"entry(cursor=" + entry.path("cursor").asLong() + ") 에 explanation_result 가 없다 — 계약 위반");
		}
		return result;
	}
}
