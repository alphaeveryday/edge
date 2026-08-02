package com.edge.superadmin.repository;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

/**
 * 장중 1분 파이프라인 원장({@code minute_*}) 요약 관측(ALPHA-651).
 *
 * <p>편입은 <b>행 복제가 아니라 요약 관측</b>이다 — 1분 쪽 expected/actual 은 이미
 * {@code minute_ingestion_window}(장 시작 시 하루치 materialize)가 갖고 있어, 매분
 * {@code ops_expected_task} 행을 만들면 같은 사실이 두 원장에 산다(계획 §2-1 금지).
 * 이 인터페이스는 읽기전용이다 — minute 원장은 실행을 제어하는 원장이라 콘솔이 쓰면 안 된다.
 *
 * <p><b>이 신호가 틀리면 어느 방향인가</b>: MISSING 판정은 EOD QC 소관이라, 실행체가 죽으면
 * 창은 DUE 로 <b>잔류</b>한다. MISSING 만 세면 죽은 실행체가 "결손 0"으로 보인다(원장이
 * 관대해지는 방향). 그래서 기한이 지난 DUE/CLAIMED 를 {@code overdueNoEvidence} 로 여기서
 * 직접 센다 — "안 돌았다(무증거)"와 "돌았는데 빈 데이터(VALID_EMPTY)"가 구분되는 지점이다.
 */
public interface MinuteStatusRepository {

	/** 해당 세션 날짜의 요약 — 세션 부재는 빈 목록(그 자체가 "미가동" 사실)이다. */
	MinuteStatus status(LocalDate sessionDate);

	record MinuteStatus(List<SessionSummary> sessions, JobCounts newsJobs) {
	}

	/**
	 * 세션 하나의 요약. 서버 시계 판정은 {@code leaseExpired}·{@code windows.overdueNoEvidence}·
	 * {@code gaps[].noEvidence}·{@code priceJobs.claimedExpired} 네 축이고 나머지는 원장 원문이다.
	 * lease 는 실행체 스스로 유지하는 생존 계약(fencing)이라 만료가 곧 "실행체 증거 끊김"이다.
	 * lease 가 아예 없으면(null) 판정 불가라 null — "기동 증거 자체가 없음"과 만료를 뭉개지 않는다.
	 */
	record SessionSummary(String sessionId, String dataset, String sourceGroup,
			LocalDate sessionDate, String phase, String universeVersion, int expectedWindowCount,
			OffsetDateTime processedThrough, OffsetDateTime contiguousCompleteThrough,
			OffsetDateTime heartbeatAt, OffsetDateTime leaseExpiresAt, Boolean leaseExpired,
			WindowCounts windows, List<GapWindow> gaps, JobCounts priceJobs) {
	}

	/**
	 * 창 상태 집계 — 원장 어휘 7종 그대로 + 파생 1개({@code overdueNoEvidence} =
	 * {@code window_end <= now()} 인 DUE·CLAIMED). CLAIMED 도 포함한다 — claim 만 있고 커밋이
	 * 없는 과거 창은 데이터 증거가 없는 창이다.
	 */
	record WindowCounts(long due, long claimed, long valid, long validEmpty, long incomplete,
			long missing, long invalid, long overdueNoEvidence) {
	}

	/** 결손·무증거 창 하나 — "그 구간이 어디인가"의 근거 목록(집계만 있는 화면 금지). */
	record GapWindow(OffsetDateTime windowStart, OffsetDateTime windowEnd, String dataStatus,
			boolean noEvidence) {
	}

	/**
	 * job 상태 집계 — {@code waiting} 은 PENDING+RETRY_WAIT(재시도 대기 포함 미귀결).
	 * {@code claimedExpired} 는 claimed 중 유효한 lease 가 없는 것(만료 또는 NULL — writer 의
	 * 회수 조건과 동일, 서버 시계 판정). Consumer 가 죽고 아무도 재청구하지 않은 고착 후보다.
	 * "처리 중"에 뭉개면 영원히 경고가 없다.
	 */
	record JobCounts(long waiting, long claimed, long claimedExpired, long succeeded, long dead) {
	}
}
