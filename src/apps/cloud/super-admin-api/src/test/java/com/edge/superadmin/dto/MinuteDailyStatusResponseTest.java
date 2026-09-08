package com.edge.superadmin.dto;

import com.edge.superadmin.repository.MinuteStatusRepository.DailySessionSummary;
import com.edge.superadmin.repository.MinuteStatusRepository.DailyStatus;
import com.edge.superadmin.repository.MinuteStatusRepository.DailyWindowCounts;
import com.edge.superadmin.repository.MinuteStatusRepository.JobCounts;
import org.junit.jupiter.api.Test;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class MinuteDailyStatusResponseTest {

	private static final LocalDate DAY = LocalDate.of(2026, 8, 3);
	private static final JobCounts NO_JOBS = new JobCounts(0, 0, 0, 0, 0, 0);

	@Test
	void 일부_vendor_실패는_주의이고_전량_실패는_장애다() {
		MinuteDailyStatusResponse partial = response(List.of(
				session("kis", "FAILED", null, windows(390, 0)),
				session("toss", "FINALIZED", false, windows(390, 0))));
		MinuteDailyStatusResponse all = response(List.of(
				session("kis", "FAILED", null, windows(390, 0)),
				session("toss", "FAILED", null, windows(390, 0))));

		assertThat(first(partial).state()).isEqualTo(MinuteDailyStatusResponse.State.CAUTION);
		assertThat(first(partial).failedSessions()).isEqualTo(1);
		assertThat(first(partial).basis()).contains("kis 세션 실패");
		assertThat(first(all).state()).isEqualTo(MinuteDailyStatusResponse.State.FAILURE);
		assertThat(first(all).failedSessions()).isEqualTo(2);
	}

	@Test
	void 기한지난_PLANNED는_장애이고_DRAINED와_QC_RUNNING은_완료가_아니다() {
		DailyWindowCounts overdue = new DailyWindowCounts(390, 0, 0, 0, 0, 0, 0, 390, 0);
		assertThat(first(response(List.of(
				session("kis", "PLANNED", null, overdue)))).state())
				.isEqualTo(MinuteDailyStatusResponse.State.FAILURE);
		assertThat(first(response(List.of(
				session("kis", "DRAINED", null, windows(390, 0))))).state())
				.isEqualTo(MinuteDailyStatusResponse.State.RUNNING);
		assertThat(first(response(List.of(
				session("kis", "QC_RUNNING", null, windows(390, 0))))).state())
				.isEqualTo(MinuteDailyStatusResponse.State.RUNNING);
		assertThat(first(response(List.of(session("kis", "PLANNED", null,
				windows(0, 0), new JobCounts(0, 0, 0, 0, 0, 1))))).state())
				.isEqualTo(MinuteDailyStatusResponse.State.CAUTION);
		assertThat(first(response(List.of(
				session("kis", "PLANNED", null, windows(0, 0))))).state())
				.as("계획 세션도 기대 창과 원장이 어긋나면 정상 대기로 위장하지 않는다")
				.isEqualTo(MinuteDailyStatusResponse.State.CAUTION);
	}

	@Test
	void 정상_empty는_정상이고_부분실패와_원장불일치는_주의다() {
		assertThat(first(response(List.of(
				session("kis", "FINALIZED", false, windows(0, 390))))).state())
				.isEqualTo(MinuteDailyStatusResponse.State.NORMAL);
		assertThat(first(response(List.of(
				session("kis", "FINALIZED", false, windows(389, 0, 1))))).state())
				.isEqualTo(MinuteDailyStatusResponse.State.CAUTION);
		assertThat(first(response(List.of(
				session("kis", "FINALIZED", false, windows(389, 0))))).state())
				.isEqualTo(MinuteDailyStatusResponse.State.CAUTION);
	}

	@Test
	void FINALIZED라도_후속_job이_남으면_완료로_확정하지_않는다() {
		assertThat(first(response(List.of(session("kis", "FINALIZED", false,
				windows(390, 0), new JobCounts(1, 0, 0, 0, 0, 0))))).state())
				.isEqualTo(MinuteDailyStatusResponse.State.WAITING);
		assertThat(first(response(List.of(session("kis", "FINALIZED", false,
				windows(390, 0), new JobCounts(0, 1, 0, 0, 0, 0))))).state())
				.isEqualTo(MinuteDailyStatusResponse.State.RUNNING);
	}

	@Test
	void 실행중_lease만료는_장애이고_세션없는_고착뉴스job은_주의로_드러난다() {
		assertThat(first(response(List.of(
				session("kis", "ACTIVE", true, windows(390, 0))))).state())
				.isEqualTo(MinuteDailyStatusResponse.State.FAILURE);

		MinuteDailyStatusResponse news = MinuteDailyStatusResponse.from(1, DAY, DAY,
				new DailyStatus(List.of(), Map.of(DAY, new JobCounts(0, 0, 1, 0, 2, 0))));
		assertThat(first(news).dataset()).isEqualTo("news_minute");
		assertThat(first(news).state()).isEqualTo(MinuteDailyStatusResponse.State.CAUTION);
		assertThat(first(news).totalSessions()).isZero();

		MinuteDailyStatusResponse deliveryFailed = MinuteDailyStatusResponse.from(1, DAY, DAY,
				new DailyStatus(List.of(), Map.of(DAY, new JobCounts(0, 0, 0, 0, 0, 1))));
		assertThat(first(deliveryFailed).state()).isEqualTo(MinuteDailyStatusResponse.State.CAUTION);
		assertThat(first(deliveryFailed).basis()).contains("전달 실패 1");
	}

	@Test
	void 날짜축_뉴스job은_특정_vendor_세션의_결함으로_귀속하지_않는다() {
		List<DailySessionSummary> sessions = List.of(
				newsSession("bigkinds-a", "FINALIZED"),
				newsSession("bigkinds-b", "FINALIZED"));
		JobCounts deliveryFailed = new JobCounts(0, 0, 0, 0, 0, 2);

		MinuteDailyStatusResponse response = MinuteDailyStatusResponse.from(1, DAY, DAY,
				new DailyStatus(sessions, Map.of(DAY, deliveryFailed)));

		assertThat(first(response).state()).isEqualTo(MinuteDailyStatusResponse.State.CAUTION);
		assertThat(first(response).basis())
				.as("session_id 없는 뉴스 job을 첫 vendor 실패처럼 표시하면 안 된다")
				.contains("날짜 축").doesNotContain("bigkinds-a", "bigkinds-b");
		assertThat(first(response).failedSessions()).isZero();

		MinuteDailyStatusResponse waiting = MinuteDailyStatusResponse.from(1, DAY, DAY,
				new DailyStatus(sessions, Map.of(DAY, new JobCounts(3, 0, 0, 0, 0, 0))));
		assertThat(first(waiting).state()).isEqualTo(MinuteDailyStatusResponse.State.WAITING);
		assertThat(first(waiting).basis()).contains("날짜 축");
	}

	@Test
	void 세션없는_날짜에도_미종결_뉴스job은_계획없음으로_사라지지_않는다() {
		MinuteDailyStatusResponse waiting = MinuteDailyStatusResponse.from(1, DAY, DAY,
				new DailyStatus(List.of(), Map.of(DAY, new JobCounts(2, 0, 0, 0, 0, 0))));
		MinuteDailyStatusResponse running = MinuteDailyStatusResponse.from(1, DAY, DAY,
				new DailyStatus(List.of(), Map.of(DAY, new JobCounts(0, 2, 0, 0, 0, 0))));

		assertThat(first(waiting).state()).isEqualTo(MinuteDailyStatusResponse.State.WAITING);
		assertThat(first(running).state()).isEqualTo(MinuteDailyStatusResponse.State.RUNNING);
		assertThat(first(waiting).totalSessions()).isZero();
	}

	@Test
	void 뉴스세션_주의근거는_정상_job진행_문구로_덮지_않는다() {
		DailySessionSummary mismatch = new DailySessionSummary("news_minute", "bigkinds", DAY,
				"FINALIZED", 390, false, windows(389, 0), NO_JOBS);
		MinuteDailyStatusResponse response = MinuteDailyStatusResponse.from(1, DAY, DAY,
				new DailyStatus(List.of(mismatch),
						Map.of(DAY, new JobCounts(4, 0, 0, 0, 0, 0))));

		assertThat(first(response).state()).isEqualTo(MinuteDailyStatusResponse.State.CAUTION);
		assertThat(first(response).basis()).contains("품질·원장 결함").doesNotContain("job 대기");
	}

	private static MinuteDailyStatusResponse response(List<DailySessionSummary> sessions) {
		return MinuteDailyStatusResponse.from(1, DAY, DAY, new DailyStatus(sessions, Map.of()));
	}

	private static MinuteDailyStatusResponse.DatasetResponse first(MinuteDailyStatusResponse response) {
		return response.dates().get(0).datasets().get(0);
	}

	private static DailySessionSummary session(String source, String phase, Boolean leaseExpired,
			DailyWindowCounts windows) {
		return session(source, phase, leaseExpired, windows, NO_JOBS);
	}

	private static DailySessionSummary newsSession(String source, String phase) {
		return new DailySessionSummary("news_minute", source, DAY, phase, 390,
				false, windows(390, 0), NO_JOBS);
	}

	private static DailySessionSummary session(String source, String phase, Boolean leaseExpired,
			DailyWindowCounts windows, JobCounts jobs) {
		return new DailySessionSummary("price_minute", source, DAY, phase, 390,
				leaseExpired, windows, jobs);
	}

	private static DailyWindowCounts windows(long valid, long validEmpty) {
		return windows(valid, validEmpty, 0);
	}

	private static DailyWindowCounts windows(long valid, long validEmpty, long failedUnitWindows) {
		return new DailyWindowCounts(0, 0, valid, validEmpty, 0, 0, 0, 0,
				failedUnitWindows);
	}
}
