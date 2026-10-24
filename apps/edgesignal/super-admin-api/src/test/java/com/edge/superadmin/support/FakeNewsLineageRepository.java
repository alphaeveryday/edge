package com.edge.superadmin.support;

import com.edge.superadmin.repository.NewsLineageRepository;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

/**
 * standalone 컨트롤러 테스트용 손 페이크(레포 hand-fake 관례). 날짜별 결과를 주입한다 —
 * 날짜 무시하고 같은 값을 돌려주면 "date 필터가 실제로 전달되는가"를 검증하는 테스트가
 * 구조적으로 통과할 수 없다(Rule 9). null 키는 "전체 누적"이다.
 *
 * <p>단계(Stage) 필터는 문서 행의 축(assertionCount·usedInAnalysis)으로 직접 적용한다 —
 * 무시하고 전량을 돌려주면 "stage 가 리포지토리까지 전달되는가" 테스트가 통과할 수 없다.
 */
public class FakeNewsLineageRepository implements NewsLineageRepository {

	public static final LineageSummary EMPTY = new LineageSummary(0, 0, 0);
	public static final ExtractionSummary NO_EXTRACTION =
			new ExtractionSummary(0, 0, List.of());

	private final Map<LocalDate, LineageSummary> summaries;
	private final Map<LocalDate, List<LineageDocument>> documents;
	private final Map<LocalDate, ExtractionSummary> extractions;

	public FakeNewsLineageRepository() {
		this(Map.of(), Map.of(), Map.of());
	}

	public FakeNewsLineageRepository(Map<LocalDate, LineageSummary> summaries,
			Map<LocalDate, List<LineageDocument>> documents) {
		this(summaries, documents, Map.of());
	}

	public FakeNewsLineageRepository(Map<LocalDate, LineageSummary> summaries,
			Map<LocalDate, List<LineageDocument>> documents,
			Map<LocalDate, ExtractionSummary> extractions) {
		// HashMap 복사 — "전체 누적" 키가 null 인데 Map.of 계열은 null 키 조회에서 NPE 다.
		this.summaries = new java.util.HashMap<>(summaries);
		this.documents = new java.util.HashMap<>(documents);
		this.extractions = new java.util.HashMap<>(extractions);
	}

	@Override
	public LineageSummary summary(LocalDate dateKst) {
		return summaries.getOrDefault(dateKst, EMPTY);
	}

	@Override
	public List<LineageDocument> documents(LocalDate dateKst, Stage stage, int limit) {
		List<LineageDocument> all = documents.getOrDefault(dateKst, List.of()).stream()
				.filter(d -> switch (stage == null ? "all" : stage.name()) {
					case "STRUCTURED" -> d.assertionCount() > 0;
					case "UNSTRUCTURED" -> d.assertionCount() == 0;
					case "USED" -> d.usedInAnalysis();
					default -> true;
				})
				.toList();
		return all.subList(0, Math.min(limit, all.size()));
	}

	@Override
	public ExtractionSummary extraction(LocalDate dateKst) {
		return extractions.getOrDefault(dateKst, NO_EXTRACTION);
	}

	@Override
	public Lineage lineage(LocalDate dateKst, Stage stage, int limit) {
		return new Lineage(summary(dateKst), documents(dateKst, stage, limit),
				extraction(dateKst));
	}
}
