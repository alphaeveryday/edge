package com.edge.superadmin.support;

import com.edge.superadmin.repository.ConsoleFactsRepository;

import java.time.LocalDate;
import java.util.List;

/**
 * standalone 컨트롤러 테스트용 {@link ConsoleFactsRepository} 손 페이크(레포 hand-fake 관례).
 *
 * <p>주입한 사실을 그대로 돌려주되 <b>요청한 날짜를 기록</b>한다 — date 파라미터가 아래로
 * 흘러가는지 검증하는 테스트가 구조적으로 통과해 버리면 안 된다(Rule 9).
 */
public class FakeConsoleFactsRepository implements ConsoleFactsRepository {

	private final ConsoleFacts facts;

	/** 마지막으로 요청된 날짜. */
	public LocalDate requestedDate;
	public LocalDate requestedTrendDate;
	public List<EntityResolutionPoint> trend = List.of();
	public LocalDate requestedIntradayMaxDate;
	public int requestedIntradayDays;
	public IntradayAnalysisTrend intradayTrend;

	public FakeConsoleFactsRepository(ConsoleFacts facts) {
		this.facts = facts;
	}

	@Override
	public ConsoleFacts facts(LocalDate date) {
		this.requestedDate = date;
		return facts;
	}

	@Override
	public List<EntityResolutionPoint> entityResolutionTrend(LocalDate date) {
		this.requestedTrendDate = date;
		return trend;
	}

	@Override
	public IntradayAnalysisTrend intradayAnalysisTrend(LocalDate maxDate, int days) {
		this.requestedIntradayMaxDate = maxDate;
		this.requestedIntradayDays = days;
		return intradayTrend;
	}
}
