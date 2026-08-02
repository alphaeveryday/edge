package com.edge.superadmin.dto;

import java.util.List;

/**
 * Run Overview 응답 — 레인(pipeline_type)별 최신 런의 운영 요약(ALPHA-683).
 *
 * <p>필드는 super-admin-ui sources 타입과 동일한 camelCase. {@code opsStatus} 는 모니터링 판정
 * 스펙 §7 의 <b>Run 집계 어휘</b>(IN_PROGRESS·READY·DEGRADED·BLOCKED·UNKNOWN)다 — 원장 4축을
 * 재명명한 것이 아니라 스펙이 별도로 정의한 파생 요약이고, 파생 규칙은 서비스 한 곳에만 있다.
 *
 * <p>{@code counts} 의 귀결 수(fulfilled·failed·missed·blocked·pending)는 <b>필수(required)
 * DUE 작업 기준</b>이다 — "오늘 발행 가능한가"의 분모는 필수 작업이다(멘토 지적: 단위 없는
 * 숫자 금지 — UI 는 반드시 "필수 작업 N개 중" 형태로 낸다). {@code due} 만 전체 DUE 수다.
 *
 * <p>{@code defects} 는 파이프라인 순서(stage raw→normalize→feature, task_key)라 <b>첫 원소가
 * 최초 결함 지점</b>이다. 빈 배열이면 결함 없음.
 */
public record SourceOverviewResponse(List<LaneResponse> lanes) {

	public record LaneResponse(String pipelineType, String runKey, String tradingDate,
			String launchStatus, String orchestrationStatus, String opsStatus,
			CountsResponse counts, List<DefectResponse> defects) {
	}

	/** {@code skipped} 는 계획 축(할 일이 아니었다)이라 귀결 수와 분모가 다르다. */
	public record CountsResponse(int due, int requiredDue, int fulfilled, int failed,
			int missed, int blocked, int pending, int skipped) {
	}

	/**
	 * 결함 하나 — 원장 축 원문 그대로(표시 라벨은 UI 소관). {@code overdue} 만 서버 판정이다:
	 * "PENDING 인데 deadline 경과"는 시계가 필요해 화면에서 재계산하면 클라이언트 시계에
	 * 좌우된다(서버가 판정한다 — running 플래그와 같은 이유).
	 */
	public record DefectResponse(String stage, String taskKey, String outcome,
			String dataStatus, String freshnessStatus, Long failedRecords, boolean overdue) {
	}
}
