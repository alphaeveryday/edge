package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.mock.ExplanationMockStore;
import com.edge.tenantconsole.mock.ExplanationMockStore.Explanation;
import com.edge.tenantconsole.mock.ExplanationMockStore.FeedStatus;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * 가격 변동 설명 mock 표면(ALPHA-513) — 검증·not-found 판정만 하고 mock 스토어에
 * 위임한다. DB 연동 시 스토어 의존을 repository 로 교체한다.
 */
@Service
public class ExplanationService {

	private final ExplanationMockStore store;

	public ExplanationService(ExplanationMockStore store) {
		this.store = store;
	}

	public List<Explanation> list() {
		return store.findAll();
	}

	public FeedStatus feedStatus() {
		return store.feedStatus();
	}

	public void updateFinal(long id, String finalText) {
		// 최종 제공 문구는 고객 노출 문면 — 빈 문구로의 교체는 막는다.
		requireText(finalText);
		ensure(store.updateFinal(id, finalText));
	}

	public void stop(long id) {
		ensure(store.stop(id));
	}

	public void moveToReview(long id) {
		ensure(store.moveToReview(id));
	}

	public void approve(long id, String finalText) {
		requireText(finalText);
		ensure(store.approve(id, finalText));
	}

	public void reject(long id, String note) {
		if (note == null || note.isBlank()) {
			// 반려 사유는 감사 재현의 최소 단서다(state-machine.md 정정·검수 규율).
			throw new GeneralException(ConsoleErrorStatus.REASON_REQUIRED);
		}
		ensure(store.reject(id));
	}

	public void saveDraft(long id, String finalText) {
		// 임시 저장은 비우는 중간 상태를 허용한다 — null 만 거른다.
		if (finalText == null) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		ensure(store.saveDraft(id, finalText));
	}

	private static void requireText(String text) {
		if (text == null || text.isBlank()) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
	}

	private static void ensure(boolean found) {
		if (!found) {
			throw new GeneralException(ConsoleErrorStatus.EXPLANATION_NOT_FOUND);
		}
	}
}
