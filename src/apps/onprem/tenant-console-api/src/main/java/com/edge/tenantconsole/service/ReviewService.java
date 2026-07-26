package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.dto.ReviewApproveRequest;
import com.edge.tenantconsole.entity.AnalysisItemStatusHistoryEntity;
import com.edge.tenantconsole.entity.ReviewTaskEntity;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.model.ReviewItem;
import com.edge.tenantconsole.repository.AnalysisItemStatusHistoryRepository;
import com.edge.tenantconsole.repository.PublicationRepository;
import com.edge.tenantconsole.repository.ReviewItemRepository;
import com.edge.tenantconsole.repository.ReviewTaskRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Limit;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

/**
 * 검수 오케스트레이션(state-machine.md·ALPHA-437): 승인 = REVIEW_REQUIRED → APPROVED +
 * 재발행(수정 승인은 편집 문구를 publication.published_summary 로 게시), 반려 =
 * REJECTED(사유 필수), 차단 = BLOCKED(사유 필수, 게시 무접촉). 전이·게시·review_task
 * 기록·감사(console_action_log)는 한 트랜잭션 — 어중간한 상태·기록 없는 결정을 남기지
 * 않는다. 편집 원문은 analysis_item 에 보존된다(review_task DDL 규약).
 */
@Service
public class ReviewService {

	private static final Logger log = LoggerFactory.getLogger(ReviewService.class);
	private static final int LIST_LIMIT = 100;

	private final ReviewItemRepository reviewItemRepository;
	private final PublicationRepository publicationRepository;
	private final ReviewTaskRepository reviewTaskRepository;
	private final AnalysisItemStatusHistoryRepository statusHistoryRepository;
	private final ConsoleActionLogService actionLog;

	public ReviewService(ReviewItemRepository reviewItemRepository,
			PublicationRepository publicationRepository,
			ReviewTaskRepository reviewTaskRepository,
			AnalysisItemStatusHistoryRepository statusHistoryRepository,
			ConsoleActionLogService actionLog) {
		this.reviewItemRepository = reviewItemRepository;
		this.publicationRepository = publicationRepository;
		this.reviewTaskRepository = reviewTaskRepository;
		this.statusHistoryRepository = statusHistoryRepository;
		this.actionLog = actionLog;
	}

	public List<ReviewItem> list(String status) {
		return reviewItemRepository.findByStatusOrderByReceivedAtAsc(status, Limit.of(LIST_LIMIT))
				.stream().map(ReviewItem::from).toList();
	}

	@Transactional
	public void approve(String explanationResultId, ReviewApproveRequest request,
			SessionMember actor, String clientIp) {
		String editedSummary = normalizeEdit(request == null ? null : request.editedSummary());
		String editedHeadline = normalizeEdit(request == null ? null : request.editedHeadline());
		String note = blankToNull(request == null ? null : request.note());
		boolean edited = editedSummary != null || editedHeadline != null;
		ReviewItem item = reviewItemRepository.findById(explanationResultId).map(ReviewItem::from)
				.orElseThrow(() -> new GeneralException(ConsoleErrorStatus.REVIEW_ITEM_NOT_FOUND));
		if (item.etfTicker() == null) {
			// ticker 는 게시(서빙 키) 필수 — 없는 항목은 승인해도 노출할 수 없다.
			throw new GeneralException(ConsoleErrorStatus.NOT_PUBLISHABLE);
		}
		if (reviewItemRepository.decide(explanationResultId, "APPROVED") == 0) {
			throw new GeneralException(ConsoleErrorStatus.NOT_IN_REVIEW);
		}
		// 게시 문구 스냅샷 — 수정 승인은 편집 요약, 일반 승인은 원문(published_summary 규약).
		String publishedSummary = editedSummary != null ? editedSummary : item.summary();
		if (publicationRepository.publish(explanationResultId, item.etfTicker(), item.tradeDate(),
				publishedSummary) == 0) {
			// grain 선점 — 전이도 함께 롤백된다(같은 트랜잭션). 검수자는 기존 게시를 내린 뒤 재시도.
			throw new GeneralException(ConsoleErrorStatus.GRAIN_OCCUPIED);
		}
		reviewTaskRepository.save(new ReviewTaskEntity(explanationResultId,
				edited ? "EDITED_APPROVED" : "APPROVED", actor.memberId(),
				editedHeadline, editedSummary, note, OffsetDateTime.now()));
		// 자기 전이는 같은 트랜잭션에서 이력 원장에 기록한다(status_history writer 규약).
		statusHistoryRepository.save(new AnalysisItemStatusHistoryEntity(explanationResultId,
				"REVIEW_REQUIRED", "APPROVED", "MEMBER", actor.memberId(), note));
		actionLog.record(actor, edited ? "REVIEW_EDITED_APPROVED" : "REVIEW_APPROVED",
				"ANALYSIS_ITEM", explanationResultId,
				Map.of("ticker", item.etfTicker(), "tradeDate", String.valueOf(item.tradeDate())),
				clientIp);
		log.info("review approved id={} ticker={} trade_date={} edited={}",
				explanationResultId, item.etfTicker(), item.tradeDate(), edited);
	}

	@Transactional
	public void reject(String explanationResultId, String reason, SessionMember actor,
			String clientIp) {
		if (reason == null || reason.isBlank()) {
			// 반려 사유는 감사 재현의 최소 단서다(state-machine.md 정정·검수 규율).
			throw new GeneralException(ConsoleErrorStatus.REASON_REQUIRED);
		}
		if (reviewItemRepository.findById(explanationResultId).isEmpty()) {
			throw new GeneralException(ConsoleErrorStatus.REVIEW_ITEM_NOT_FOUND);
		}
		if (reviewItemRepository.decide(explanationResultId, "REJECTED") == 0) {
			throw new GeneralException(ConsoleErrorStatus.NOT_IN_REVIEW);
		}
		reviewTaskRepository.save(new ReviewTaskEntity(explanationResultId, "REJECTED",
				actor.memberId(), null, null, reason, OffsetDateTime.now()));
		statusHistoryRepository.save(new AnalysisItemStatusHistoryEntity(explanationResultId,
				"REVIEW_REQUIRED", "REJECTED", "MEMBER", actor.memberId(), reason));
		actionLog.record(actor, "REVIEW_REJECTED", "ANALYSIS_ITEM", explanationResultId,
				Map.of("reason", reason), clientIp);
		log.info("review rejected id={} reason={}", explanationResultId, reason);
	}

	@Transactional
	public void block(String explanationResultId, String reason, SessionMember actor,
			String clientIp) {
		if (reason == null || reason.isBlank()) {
			// 차단 사유도 반려와 같은 규율 — 감사 재현의 최소 단서다.
			throw new GeneralException(ConsoleErrorStatus.REASON_REQUIRED);
		}
		if (reviewItemRepository.findById(explanationResultId).isEmpty()) {
			throw new GeneralException(ConsoleErrorStatus.REVIEW_ITEM_NOT_FOUND);
		}
		if (reviewItemRepository.decide(explanationResultId, "BLOCKED") == 0) {
			throw new GeneralException(ConsoleErrorStatus.NOT_IN_REVIEW);
		}
		// 차단은 게시 무접촉·review_task 어휘 밖(ck_review_task_status) — 사유·주체는
		// 이력 원장과 감사 로그가 담는다.
		statusHistoryRepository.save(new AnalysisItemStatusHistoryEntity(explanationResultId,
				"REVIEW_REQUIRED", "BLOCKED", "MEMBER", actor.memberId(), reason));
		actionLog.record(actor, "REVIEW_BLOCKED", "ANALYSIS_ITEM", explanationResultId,
				Map.of("reason", reason), clientIp);
		log.info("review blocked id={} reason={}", explanationResultId, reason);
	}

	/** 편집 필드 정규화 — 공백뿐이면 400: 편집 의도가 일반 승인으로 조용히 강등되는 것을 막는다. */
	private static String normalizeEdit(String value) {
		if (value == null) {
			return null;
		}
		if (value.isBlank()) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		return value.trim();
	}

	private static String blankToNull(String value) {
		return value == null || value.isBlank() ? null : value.trim();
	}
}
