package com.edge.superadmin.support;

import com.edge.superadmin.repository.NewsLineageRepository;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

/**
 * standalone 컨트롤러 테스트용 손 페이크(레포 hand-fake 관례). 날짜별 결과를 주입한다 —
 * 날짜 무시하고 같은 값을 돌려주면 "date 필터가 실제로 전달되는가"를 검증하는 테스트가
 * 구조적으로 통과할 수 없다(Rule 9). null 키는 "전체 누적"이다.
 */
public class FakeNewsLineageRepository implements NewsLineageRepository {

	public static final LineageSummary EMPTY = new LineageSummary(0, 0, 0);

	private final Map<LocalDate, LineageSummary> summaries;
	private final Map<LocalDate, List<LineageDocument>> documents;

	public FakeNewsLineageRepository() {
		this(Map.of(), Map.of());
	}

	public FakeNewsLineageRepository(Map<LocalDate, LineageSummary> summaries,
			Map<LocalDate, List<LineageDocument>> documents) {
		// HashMap 복사 — "전체 누적" 키가 null 인데 Map.of 계열은 null 키 조회에서 NPE 다.
		this.summaries = new java.util.HashMap<>(summaries);
		this.documents = new java.util.HashMap<>(documents);
	}

	@Override
	public LineageSummary summary(LocalDate dateKst) {
		return summaries.getOrDefault(dateKst, EMPTY);
	}

	@Override
	public List<LineageDocument> documents(LocalDate dateKst, int limit) {
		List<LineageDocument> all = documents.getOrDefault(dateKst, List.of());
		return all.subList(0, Math.min(limit, all.size()));
	}
}
