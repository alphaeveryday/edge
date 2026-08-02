package com.edge.superadmin.support;

import com.edge.superadmin.repository.MinuteStatusRepository;

import java.time.LocalDate;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * standalone 컨트롤러 테스트용 손 페이크(레포 hand-fake 관례). 날짜별 결과를 주입한다 —
 * 날짜를 무시하면 "date 가 실제로 전달되는가" 검증이 구조적으로 통과 불가다(Rule 9).
 * 미주입 날짜는 세션 없음 — 실물의 "미가동" 응답과 같은 형상이다.
 */
public class FakeMinuteStatusRepository implements MinuteStatusRepository {

	private static final JobCounts NO_JOBS = new JobCounts(0, 0, 0, 0);

	private final Map<LocalDate, MinuteStatus> byDate;

	public FakeMinuteStatusRepository() {
		this(Map.of());
	}

	public FakeMinuteStatusRepository(Map<LocalDate, MinuteStatus> byDate) {
		this.byDate = new HashMap<>(byDate);
	}

	@Override
	public MinuteStatus status(LocalDate sessionDate) {
		return byDate.getOrDefault(sessionDate, new MinuteStatus(List.of(), NO_JOBS));
	}
}
