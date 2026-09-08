package com.edge.superadmin.dto;

import com.edge.superadmin.repository.MinuteStatusRepository.DailySessionSummary;
import com.edge.superadmin.repository.MinuteStatusRepository.DailyStatus;
import com.edge.superadmin.repository.MinuteStatusRepository.DailyWindowCounts;
import com.edge.superadmin.repository.MinuteStatusRepository.JobCounts;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** 최근 일별 minute 상태 — 판정은 서버 한 곳에서 끝내고 Grid 는 그대로 표시한다(ALPHA-1066). */
public record MinuteDailyStatusResponse(int days, String from, String to, List<DayResponse> dates) {

	private static final Set<String> QC_PHASES = Set.of("DRAINED", "QC_RUNNING");
	private static final JobCounts NO_JOBS = new JobCounts(0, 0, 0, 0, 0, 0);

	public enum State {
		NORMAL, CAUTION, FAILURE, RUNNING, WAITING
	}

	public record DayResponse(String date, List<DatasetResponse> datasets) {
	}

	public record DatasetResponse(String dataset, State state, String basis,
			int failedSessions, int totalSessions) {
	}

	private record SessionDecision(State state, String basis) {
	}

	public static MinuteDailyStatusResponse from(int days, LocalDate from, LocalDate to,
			DailyStatus status) {
		Map<LocalDate, Map<String, List<DailySessionSummary>>> grouped = new LinkedHashMap<>();
		status.sessions().forEach(session -> grouped
				.computeIfAbsent(session.sessionDate(), ignored -> new LinkedHashMap<>())
				.computeIfAbsent(session.dataset(), ignored -> new ArrayList<>())
				.add(session));

		List<DayResponse> dates = new ArrayList<>();
		for (LocalDate date = from; !date.isAfter(to); date = date.plusDays(1)) {
			Map<String, List<DailySessionSummary>> byDataset = grouped.getOrDefault(date, Map.of());
			List<DatasetResponse> datasets = new ArrayList<>();
			for (Map.Entry<String, List<DailySessionSummary>> entry : byDataset.entrySet()) {
				JobCounts newsJobs = "news_minute".equals(entry.getKey())
						? status.newsJobs().getOrDefault(date, NO_JOBS) : NO_JOBS;
				datasets.add(aggregate(entry.getKey(), entry.getValue(), newsJobs));
			}
			JobCounts newsJobs = status.newsJobs().get(date);
			if (!byDataset.containsKey("news_minute") && unresolved(newsJobs)) {
				datasets.add(newsJobsOnly(newsJobs));
			}
			dates.add(new DayResponse(date.toString(), List.copyOf(datasets)));
		}
		return new MinuteDailyStatusResponse(days, from.toString(), to.toString(), List.copyOf(dates));
	}

	private static DatasetResponse aggregate(String dataset, List<DailySessionSummary> sessions,
			JobCounts newsJobs) {
		boolean dateAxisJobs = "news_minute".equals(dataset);
		List<SessionDecision> decisions = sessions.stream()
				.map(session -> decide(session, "price_minute".equals(dataset)
						? session.priceJobs() : NO_JOBS))
				.toList();
		long failed = decisions.stream().filter(d -> d.state() == State.FAILURE).count();
		State state;
		if (failed == decisions.size()) state = State.FAILURE;
		else if (failed > 0 || decisions.stream().anyMatch(d -> d.state() == State.CAUTION)) {
			state = State.CAUTION;
		} else if (decisions.stream().anyMatch(d -> d.state() == State.RUNNING)) state = State.RUNNING;
		else if (decisions.stream().anyMatch(d -> d.state() == State.WAITING)) state = State.WAITING;
		else state = State.NORMAL;

		State evidenceState = failed > 0 ? State.FAILURE : state;
		SessionDecision evidence = decisions.stream().filter(d -> d.state() == evidenceState).findFirst()
				.orElse(decisions.get(0));
		String basis = failed > 0
				? "실패 세션 " + failed + " / 전체 " + decisions.size() + " · " + evidence.basis()
				: evidence.basis();
		if (dateAxisJobs && stuck(newsJobs)) {
			if (state != State.FAILURE) state = State.CAUTION;
			basis = (State.NORMAL == evidence.state() ? "" : basis + " · ")
					+ jobBasis("날짜 축", newsJobs);
		} else if (dateAxisJobs && newsJobs.claimed() > newsJobs.claimedExpired()) {
			if (state == State.NORMAL || state == State.WAITING) {
				state = State.RUNNING;
				basis = "날짜 축 뉴스 후속 job 처리 중";
			}
		} else if (dateAxisJobs && newsJobs.waiting() > 0) {
			if (state == State.NORMAL) {
				state = State.WAITING;
				basis = "날짜 축 뉴스 후속 job 대기";
			}
		}
		return new DatasetResponse(dataset, state, basis, (int) failed, decisions.size());
	}

	private static SessionDecision decide(DailySessionSummary session, JobCounts jobs) {
		DailyWindowCounts windows = session.windows();
		boolean defect = windows.incomplete() + windows.missing() + windows.invalid()
				+ windows.failedUnitWindows() > 0;
		boolean ledgerMismatch = windows.materialized() != session.expectedWindowCount();
		boolean stuck = stuck(jobs);

		if ("PLANNED".equals(session.phase())) {
			if (windows.overdueNoEvidence() > 0) {
				return new SessionDecision(State.FAILURE,
						session.sourceGroup() + " 계획 후 기한 지난 무증거 창");
			}
			if (ledgerMismatch || stuck) {
				return new SessionDecision(State.CAUTION,
						session.sourceGroup() + " 계획 세션에 원장 불일치·후속 job 고착");
			}
			return new SessionDecision(State.WAITING, session.sourceGroup() + " 계획 대기");
		}
		if ("FAILED".equals(session.phase())) {
			return new SessionDecision(State.FAILURE, session.sourceGroup() + " 세션 실패");
		}
		if ("FINALIZED".equals(session.phase())) {
			if (defect || windows.overdueNoEvidence() > 0 || ledgerMismatch || stuck) {
				return new SessionDecision(State.CAUTION,
						session.sourceGroup() + " 종료 세션에 품질·원장 결함");
			}
			if (jobs.claimed() > jobs.claimedExpired()) {
				return new SessionDecision(State.RUNNING,
						session.sourceGroup() + " 후속 job 처리 중");
			}
			if (jobs.waiting() > 0) {
				return new SessionDecision(State.WAITING,
						session.sourceGroup() + " 후속 job 대기");
			}
			return new SessionDecision(State.NORMAL, session.sourceGroup() + " 정상 종료");
		}
		if (QC_PHASES.contains(session.phase())) {
			if (defect || windows.overdueNoEvidence() > 0 || ledgerMismatch || stuck) {
				return new SessionDecision(State.CAUTION,
						session.sourceGroup() + " QC 전후 품질·원장 결함");
			}
			return new SessionDecision(State.RUNNING,
					session.sourceGroup() + ("DRAINED".equals(session.phase()) ? " QC 대기" : " QC 진행 중"));
		}
		if (Boolean.TRUE.equals(session.leaseExpired()) || windows.overdueNoEvidence() > 0) {
			return new SessionDecision(State.FAILURE,
					session.sourceGroup() + " 실행 증거 끊김 또는 기한 지난 무증거 창");
		}
		if (defect || ledgerMismatch || stuck || session.leaseExpired() == null) {
			return new SessionDecision(State.CAUTION,
					session.sourceGroup() + " 품질·원장·job 확인 필요");
		}
		return new SessionDecision(State.RUNNING, session.sourceGroup() + " 세션 실행 중");
	}

	private static boolean stuck(JobCounts jobs) {
		return jobs != null && (jobs.dead() > 0 || jobs.deliveryFailed() > 0
				|| jobs.claimedExpired() > 0);
	}

	private static boolean unresolved(JobCounts jobs) {
		return jobs != null && (stuck(jobs) || jobs.waiting() > 0 || jobs.claimed() > 0);
	}

	private static DatasetResponse newsJobsOnly(JobCounts jobs) {
		if (stuck(jobs)) {
			return new DatasetResponse("news_minute", State.CAUTION,
					jobBasis("세션 없이 날짜 축", jobs), 0, 0);
		}
		if (jobs.claimed() > jobs.claimedExpired()) {
			return new DatasetResponse("news_minute", State.RUNNING,
					"세션 없이 날짜 축 뉴스 후속 job 처리 중", 0, 0);
		}
		return new DatasetResponse("news_minute", State.WAITING,
				"세션 없이 날짜 축 뉴스 후속 job 대기", 0, 0);
	}

	private static String jobBasis(String scope, JobCounts jobs) {
		return scope + " 후속 job 고착 · dead " + jobs.dead()
				+ " · 전달 실패 " + jobs.deliveryFailed()
				+ " · lease 만료 claim " + jobs.claimedExpired();
	}
}
