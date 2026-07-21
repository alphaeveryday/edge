package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.repository.PublicationRepository;
import com.edge.tenantconsole.repository.ReviewItemRepository;
import com.edge.tenantconsole.repository.ReviewItemRepository.ReviewItem;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * 검수 오케스트레이션(state-machine.md): 승인 = REVIEW_REQUIRED → APPROVED + 재발행,
 * 반려 = REVIEW_REQUIRED → REJECTED(사유 필수). 전이·게시는 한 트랜잭션 — 승인됐는데
 * 게시가 안 되는 어중간한 상태를 남기지 않는다. 수정 승인·차단·Audit 기록은 후속(ALPHA-437 잔여).
 */
@Service
public class ReviewService {

	private static final Logger log = LoggerFactory.getLogger(ReviewService.class);
	private static final int LIST_LIMIT = 100;

	private final ReviewItemRepository reviewItemRepository;
	private final PublicationRepository publicationRepository;

	public ReviewService(ReviewItemRepository reviewItemRepository,
			PublicationRepository publicationRepository) {
		this.reviewItemRepository = reviewItemRepository;
		this.publicationRepository = publicationRepository;
	}

	public List<ReviewItem> list(String status) {
		return reviewItemRepository.findByStatus(status, LIST_LIMIT);
	}

	@Transactional
	public void approve(String explanationResultId) {
		ReviewItem item = reviewItemRepository.findById(explanationResultId)
				.orElseThrow(() -> new GeneralException(ConsoleErrorStatus.REVIEW_ITEM_NOT_FOUND));
		if (item.etfTicker() == null) {
			// ticker 는 게시(서빙 키) 필수 — 없는 항목은 승인해도 노출할 수 없다.
			throw new GeneralException(ConsoleErrorStatus.NOT_PUBLISHABLE);
		}
		if (!reviewItemRepository.decide(explanationResultId, "APPROVED")) {
			throw new GeneralException(ConsoleErrorStatus.NOT_IN_REVIEW);
		}
		if (!publicationRepository.publish(explanationResultId, item.etfTicker(), item.tradeDate())) {
			// grain 선점 — 전이도 함께 롤백된다(같은 트랜잭션). 검수자는 기존 게시를 내린 뒤 재시도.
			throw new GeneralException(ConsoleErrorStatus.GRAIN_OCCUPIED);
		}
		log.info("review approved id={} ticker={} trade_date={}",
				explanationResultId, item.etfTicker(), item.tradeDate());
	}

	@Transactional
	public void reject(String explanationResultId, String reason) {
		if (reason == null || reason.isBlank()) {
			// 반려 사유는 감사 재현의 최소 단서다(state-machine.md 정정·검수 규율).
			throw new GeneralException(ConsoleErrorStatus.REASON_REQUIRED);
		}
		if (reviewItemRepository.findById(explanationResultId).isEmpty()) {
			throw new GeneralException(ConsoleErrorStatus.REVIEW_ITEM_NOT_FOUND);
		}
		if (!reviewItemRepository.decide(explanationResultId, "REJECTED")) {
			throw new GeneralException(ConsoleErrorStatus.NOT_IN_REVIEW);
		}
		log.info("review rejected id={} reason={}", explanationResultId, reason);
	}
}
