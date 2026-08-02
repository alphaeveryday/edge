package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.dto.SourceGridResponse;
import com.edge.superadmin.dto.SourceOverviewResponse;
import com.edge.superadmin.dto.SourceOverviewResponse.CountsResponse;
import com.edge.superadmin.dto.SourceOverviewResponse.DefectResponse;
import com.edge.superadmin.dto.SourceOverviewResponse.LaneResponse;
import com.edge.superadmin.dto.SourceReportResponse;
import com.edge.superadmin.error.AdminErrorStatus;
import com.edge.superadmin.repository.PipelineStatusRepository;
import com.edge.superadmin.repository.PipelineStatusRepository.OverviewLane;
import com.edge.superadmin.repository.PipelineStatusRepository.OverviewTask;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.List;

/**
 * sources 화면 — 운영 원장(`ops_*`)의 런 하나를 읽어 그대로 낸다(ALPHA-514, 드릴다운 574).
 *
 * <p>여기서 상태를 요약·판정하지 않는다. 원장이 이미 4축(plan_status·task_outcome·
 * execution_status·data_status)으로 판정을 끝냈고, 그걸 콘솔이 다시 뭉개면 <b>다섯 번째 어휘</b>가
 * 생긴다(ALPHA-181 이 새 상태 테이블을 안 만든 것과 같은 이유). 표시 라벨은 UI 소관이다.
 */
@Service
public class SourceService {

	private final PipelineStatusRepository pipelineStatus;

	public SourceService(PipelineStatusRepository pipelineStatus) {
		this.pipelineStatus = pipelineStatus;
	}

	/**
	 * @param runKey 지목할 런의 슬롯 키. <b>파라미터가 아예 없을 때만</b>(null) 최신 런이다 —
	 *               기존 호출과 동일한 동작. 공백 문자열은 "안 준 것"이 아니라 <b>그런 키의 런이
	 *               없는 것</b>이라 아래 조회를 그대로 타 404 가 된다. 공백을 부재로 관대하게
	 *               읽으면, 키를 지목했다고 믿는 화면이 최신 런을 받아 <b>옛 런으로 오독</b>한다.
	 * @throws GeneralException 지목한 런이 원장에 없으면 404. <b>빈 리포트로 대신하지 않는다</b> —
	 *                          오타 친 런 키가 "원장이 비어 있다"로 보이면 운영자는 없는 사실을
	 *                          있는 것처럼 읽는다(빈 원장은 그 자체로 정상 상태다).
	 */
	/**
	 * 실행 격자 — 최근 {@code days}일의 슬롯 전부(ALPHA-594).
	 *
	 * @param days 조회 창(일). 1 미만이면 창이 미래로 뒤집혀 <b>조용히 빈 격자</b>가 된다 —
	 *             에러도 데이터도 아닌 화면은 운영자가 "원장이 비었다"로 오독하므로 잘못된
	 *             요청으로 거절한다. 상한 366 은 무제한 스캔 방지다(원장은 무기한 보존).
	 */
	public SourceGridResponse grid(int days) {
		if (days < 1 || days > 366) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		return SourceGridResponse.from(days, pipelineStatus.grid(days));
	}

	public SourceReportResponse report(String runKey) {
		if (runKey == null) {
			return pipelineStatus.latestRun()
					.map(SourceReportResponse::from)
					.orElseGet(SourceReportResponse::empty);
		}
		return pipelineStatus.runByKey(runKey)
				.map(SourceReportResponse::from)
				.orElseThrow(() -> new GeneralException(AdminErrorStatus.RUN_NOT_FOUND));
	}

	/**
	 * Run Overview(ALPHA-683) — 레인별 최신 런의 운영 요약. 이 클래스 상단의 "요약·판정하지
	 * 않는다"는 원장 4축 어휘를 다섯 번째 어휘로 뭉개지 말라는 뜻이고, 여기의 {@code opsStatus}
	 * 는 판정 스펙 §7 이 <b>별도로 정의한 Run 집계 어휘</b>다 — 축의 재명명이 아니라 스펙
	 * 어휘의 구현이며, 파생 규칙은 이 메서드 하나에만 둔다(화면 재계산 금지).
	 */
	public SourceOverviewResponse overview() {
		OffsetDateTime now = OffsetDateTime.now();
		return new SourceOverviewResponse(
				pipelineStatus.overview().stream().map(lane -> toLane(lane, now)).toList());
	}

	private static LaneResponse toLane(OverviewLane lane, OffsetDateTime now) {
		List<OverviewTask> due = lane.tasks().stream()
				.filter(t -> "DUE".equals(t.planStatus())).toList();
		List<OverviewTask> requiredDue = due.stream().filter(OverviewTask::required).toList();
		int skipped = lane.tasks().size() - due.size();

		// SQL 이 파이프라인 순서(stage→task_key)로 내리므로 첫 원소가 곧 최초 결함 지점이다.
		List<DefectResponse> defects = requiredDue.stream()
				.filter(t -> isDefect(t, now))
				.map(t -> new DefectResponse(t.stage(), t.taskKey(), t.outcome(), t.dataStatus(),
						t.freshnessStatus(), t.failedRecords(), overdue(t, now)))
				.toList();

		CountsResponse counts = new CountsResponse(
				due.size(), requiredDue.size(),
				countOutcome(requiredDue, "FULFILLED"), countOutcome(requiredDue, "FAILED"),
				countOutcome(requiredDue, "MISSED"), countOutcome(requiredDue, "BLOCKED"),
				countOutcome(requiredDue, "PENDING"), skipped);

		return new LaneResponse(lane.pipelineType(), lane.runKey(),
				lane.tradingDate() == null ? null : lane.tradingDate().toString(),
				lane.launchStatus(), lane.orchestrationStatus(),
				opsStatus(lane, requiredDue, defects, now), counts, defects);
	}

	/**
	 * 스펙 §7 의 집계 우선순위. 스펙 순서(IN_PROGRESS→UNKNOWN→BLOCKED→DEGRADED→READY)에서
	 * <b>기동 실패만 앞으로</b> 뺐다 — LAUNCH_FAILED 런의 작업은 deadline 전 PENDING 이라
	 * 스펙 순서대로면 "진행 중"이 되는데, 아무것도 돌지 않는 런을 진행 중으로 내면 원장이
	 * 관대해지는 방향이다(기동 실패 = 이 런의 전 대상이 차단됐다는 사실이 확정돼 있다).
	 *
	 * <p>정상 SKIPPED·데이터 UNKNOWN(설계상 대다수)·NO_EVENT 는 어떤 경로로도 DEGRADED 를
	 * 만들지 않는다 — 결함 판정은 {@link #isDefect} 하나가 정의한다.
	 */
	private static String opsStatus(OverviewLane lane, List<OverviewTask> requiredDue,
			List<DefectResponse> defects, OffsetDateTime now) {
		if ("LAUNCH_FAILED".equals(lane.launchStatus())) {
			return "BLOCKED";
		}
		if ("LAUNCH_UNKNOWN".equals(lane.launchStatus())
				|| "UNKNOWN".equals(lane.orchestrationStatus())) {
			return "UNKNOWN";
		}
		boolean pendingBeforeDeadline = requiredDue.stream().anyMatch(
				t -> "PENDING".equals(t.outcome()) && !overdue(t, now));
		if ("PLANNING".equals(lane.launchStatus())
				|| "RUNNING".equals(lane.orchestrationStatus()) || pendingBeforeDeadline) {
			return "IN_PROGRESS";
		}
		return defects.isEmpty() ? "READY" : "DEGRADED";
	}

	/**
	 * 필수 DUE 작업의 결함 — 귀결 실패(FAILED·MISSED·BLOCKED), 데이터 결손(INCOMPLETE·INVALID,
	 * 격자의 결손 점과 같은 축), 유실 건수, 신선도 STALE, 마감 경과 미귀결. UNKNOWN 은 결함이
	 * 아니다 — 완전성 미배선 24작업이 설계상 UNKNOWN 이라, 넣으면 화면 전체가 상시 결함이 된다.
	 */
	private static boolean isDefect(OverviewTask t, OffsetDateTime now) {
		return "FAILED".equals(t.outcome()) || "MISSED".equals(t.outcome())
				|| "BLOCKED".equals(t.outcome())
				|| "INCOMPLETE".equals(t.dataStatus()) || "INVALID".equals(t.dataStatus())
				|| (t.failedRecords() != null && t.failedRecords() > 0)
				|| "STALE".equals(t.freshnessStatus())
				|| overdue(t, now);
	}

	/** deadline 없는 작업(선행 대기)은 경과 판정 대상이 아니다 — null 이면 false. */
	private static boolean overdue(OverviewTask t, OffsetDateTime now) {
		return "PENDING".equals(t.outcome()) && t.deadlineAt() != null
				&& t.deadlineAt().isBefore(now);
	}

	private static int countOutcome(List<OverviewTask> tasks, String outcome) {
		return (int) tasks.stream().filter(t -> outcome.equals(t.outcome())).count();
	}
}
