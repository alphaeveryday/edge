package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.mock.ScreeningMockStore;
import com.edge.tenantconsole.mock.ScreeningMockStore.AutoPublishCriteria;
import com.edge.tenantconsole.mock.ScreeningMockStore.BannedWord;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Set;

/**
 * 점검 기준 mock 표면(ALPHA-513) — 어휘 검증만 하고 mock 스토어에 위임한다.
 * DB 연동 시 스토어 의존을 repository 로 교체한다.
 */
@Service
public class ScreeningService {

	private static final Set<String> RISKS = Set.of("LOW", "MEDIUM", "HIGH");
	private static final Set<String> ACTIONS = Set.of("REVIEW", "BLOCK");
	// 자동 제공 허용 위험 상한에 HIGH 는 없다 — HIGH 는 항상 검수·차단 경로다(UI 계약).
	private static final Set<String> MAX_RISKS = Set.of("LOW", "MEDIUM");

	private final ScreeningMockStore store;

	public ScreeningService(ScreeningMockStore store) {
		this.store = store;
	}

	public List<BannedWord> listWords() {
		return store.listWords();
	}

	public void addWord(String text, String risk, String action) {
		if (text == null || text.isBlank() || !RISKS.contains(risk) || !ACTIONS.contains(action)) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		store.addWord(text, risk, action);
	}

	public void toggleWord(long id) {
		if (!store.toggleWord(id)) {
			throw new GeneralException(ConsoleErrorStatus.BANNED_WORD_NOT_FOUND);
		}
	}

	public AutoPublishCriteria getCriteria() {
		return store.getCriteria();
	}

	public void updateCriteria(Integer minSources, String maxRisk) {
		if (minSources != null && (minSources < 1 || minSources > 3)) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		if (maxRisk != null && !MAX_RISKS.contains(maxRisk)) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		store.updateCriteria(minSources, maxRisk);
	}

	public String getDisclaimer() {
		return store.getDisclaimer();
	}

	public void updateDisclaimer(String text) {
		if (text == null || text.isBlank()) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		store.updateDisclaimer(text);
	}
}
