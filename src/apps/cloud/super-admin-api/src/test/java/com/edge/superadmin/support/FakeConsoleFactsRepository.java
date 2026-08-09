package com.edge.superadmin.support;

import com.edge.superadmin.repository.ConsoleFactsRepository;

import java.time.LocalDate;

/**
 * standalone 컨트롤러 테스트용 {@link ConsoleFactsRepository} 손 페이크(레포 hand-fake 관례).
 *
 * <p>주입한 사실을 그대로 돌려주되 <b>요청한 날짜를 기록</b>한다 — date 파라미터가 아래로
 * 흘러가는지 검증하는 테스트가 구조적으로 통과해 버리면 안 된다(Rule 9).
 */
public class FakeConsoleFactsRepository implements ConsoleFactsRepository {

	private final ConsoleFacts facts;

	/** 마지막으로 요청된 날짜. 생략 요청이면 null 이다. */
	public LocalDate requestedDate;

	public FakeConsoleFactsRepository(ConsoleFacts facts) {
		this.facts = facts;
	}

	@Override
	public ConsoleFacts facts(LocalDate date) {
		this.requestedDate = date;
		return facts;
	}
}
