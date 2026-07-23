package com.edge.tenantconsole.mock;

import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;

/**
 * screening 도메인 mock 데이터 — 금칙어·자동 제공 기준·면책 문구. UI 시안(v0.2)
 * 목데이터 이식 in-memory 가변 스토어. DB 연동 시 service 의 이 스토어 호출부를
 * repository 로 교체한다(ALPHA-513).
 */
@Component
public class ScreeningMockStore {

	public record BannedWord(long id, String text, String risk, String action, boolean active,
			String registeredAt) {
	}

	public record AutoPublishCriteria(int minSources, String maxRisk) {
	}

	private final List<BannedWord> words = new ArrayList<>(List.of(
			new BannedWord(1, "급등 확실", "HIGH", "BLOCK", true, "2026-05-02"),
			new BannedWord(2, "무조건", "HIGH", "BLOCK", true, "2026-05-02"),
			new BannedWord(3, "매수 추천", "HIGH", "BLOCK", true, "2026-05-14"),
			new BannedWord(4, "목표가 돌파 예정", "MEDIUM", "REVIEW", true, "2026-06-01"),
			new BannedWord(5, "확실시", "MEDIUM", "REVIEW", true, "2026-06-18"),
			new BannedWord(6, "전량 매도", "HIGH", "BLOCK", false, "2026-04-20")));
	private long nextWordId = 7;

	private AutoPublishCriteria criteria = new AutoPublishCriteria(2, "MEDIUM");

	private String disclaimer =
			"본 설명은 뉴스·공시 등 공개 데이터를 기반으로 자동 생성된 참고 정보이며, "
					+ "특정 종목의 매수·매도를 권유하지 않습니다. 투자 판단과 책임은 투자자 본인에게 있습니다.";

	public synchronized List<BannedWord> listWords() {
		return List.copyOf(words);
	}

	public synchronized void addWord(String text, String risk, String action) {
		// 최신 등록이 맨 위로 — UI 목록 정렬 규약(구 mock 과 동일).
		words.add(0, new BannedWord(nextWordId++, text, risk, action, true,
				LocalDate.now().toString()));
	}

	public synchronized boolean toggleWord(long id) {
		for (int i = 0; i < words.size(); i++) {
			BannedWord w = words.get(i);
			if (w.id() == id) {
				words.set(i, new BannedWord(w.id(), w.text(), w.risk(), w.action(),
						!w.active(), w.registeredAt()));
				return true;
			}
		}
		return false;
	}

	public synchronized AutoPublishCriteria getCriteria() {
		return criteria;
	}

	/** 부분 갱신 — null 인 필드는 유지한다(PATCH 시맨틱). */
	public synchronized void updateCriteria(Integer minSources, String maxRisk) {
		criteria = new AutoPublishCriteria(
				minSources == null ? criteria.minSources() : minSources,
				maxRisk == null ? criteria.maxRisk() : maxRisk);
	}

	public synchronized String getDisclaimer() {
		return disclaimer;
	}

	public synchronized void updateDisclaimer(String text) {
		disclaimer = text;
	}
}
