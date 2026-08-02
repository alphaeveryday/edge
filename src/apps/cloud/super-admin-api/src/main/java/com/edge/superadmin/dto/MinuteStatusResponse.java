package com.edge.superadmin.dto;

import com.edge.superadmin.repository.MinuteStatusRepository.GapWindow;
import com.edge.superadmin.repository.MinuteStatusRepository.JobCounts;
import com.edge.superadmin.repository.MinuteStatusRepository.MinuteStatus;
import com.edge.superadmin.repository.MinuteStatusRepository.SessionSummary;
import com.edge.superadmin.repository.MinuteStatusRepository.WindowCounts;

import java.time.OffsetDateTime;
import java.util.List;

/**
 * 장중 1분 파이프라인 요약 응답(ALPHA-651) — 단위: windows 는 <b>창(1분)</b>,
 * jobs 는 <b>논리 job</b>. 세션 부재는 빈 목록 — "미가동"이라는 사실이지 오류가 아니다.
 *
 * <p>서버(DB) 시계 파생은 세 필드다 — {@code overdueNoEvidence}·{@code leaseExpired}·
 * {@code claimedExpired}. 조회 시점 판정이라 고정 사실이 아니며, 나머지는 원장 원문이다.
 * 화면은 이 판정들을 재계산하지 않는다(Run Overview 의 overdue 와 같은 원칙).
 */
public record MinuteStatusResponse(String date, List<SessionResponse> sessions,
		JobCountsResponse newsJobs) {

	public record SessionResponse(String sessionId, String dataset, String sourceGroup,
			String phase, String universeVersion, int expectedWindowCount,
			String processedThrough, String contiguousCompleteThrough,
			String heartbeatAt, String leaseExpiresAt, Boolean leaseExpired,
			WindowCountsResponse windows, List<GapWindowResponse> gaps,
			JobCountsResponse priceJobs) {

		static SessionResponse from(SessionSummary s) {
			return new SessionResponse(s.sessionId(), s.dataset(), s.sourceGroup(), s.phase(),
					s.universeVersion(), s.expectedWindowCount(),
					iso(s.processedThrough()), iso(s.contiguousCompleteThrough()),
					iso(s.heartbeatAt()), iso(s.leaseExpiresAt()), s.leaseExpired(),
					WindowCountsResponse.from(s.windows()),
					s.gaps().stream().map(GapWindowResponse::from).toList(),
					JobCountsResponse.from(s.priceJobs()));
		}
	}

	public record WindowCountsResponse(long due, long claimed, long valid, long validEmpty,
			long incomplete, long missing, long invalid, long overdueNoEvidence) {

		static WindowCountsResponse from(WindowCounts w) {
			return new WindowCountsResponse(w.due(), w.claimed(), w.valid(), w.validEmpty(),
					w.incomplete(), w.missing(), w.invalid(), w.overdueNoEvidence());
		}
	}

	public record GapWindowResponse(String windowStart, String windowEnd, String dataStatus,
			boolean noEvidence) {

		static GapWindowResponse from(GapWindow g) {
			return new GapWindowResponse(iso(g.windowStart()), iso(g.windowEnd()),
					g.dataStatus(), g.noEvidence());
		}
	}

	public record JobCountsResponse(long waiting, long claimed, long claimedExpired,
			long succeeded, long dead) {

		static JobCountsResponse from(JobCounts j) {
			return new JobCountsResponse(j.waiting(), j.claimed(), j.claimedExpired(),
					j.succeeded(), j.dead());
		}
	}

	public static MinuteStatusResponse from(String date, MinuteStatus status) {
		return new MinuteStatusResponse(date,
				status.sessions().stream().map(SessionResponse::from).toList(),
				JobCountsResponse.from(status.newsJobs()));
	}

	private static String iso(OffsetDateTime t) {
		return t == null ? null : t.toString();
	}
}
