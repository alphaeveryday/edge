package com.edge.superadmin.repository;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

/**
 * 운영 원장(`ops_*`) **읽기 전용** 리포지토리 — 데이터 소스 수집 상태 화면의 데이터원(ALPHA-514).
 *
 * <p>원장의 소유 모듈은 data-pipeline 이다(ADR-0005 단일 writer). 이 콘솔은 조회만 하며 어떤
 * 상태도 쓰지 않는다 — 원장은 실행을 제어하지 않는 관측 projection 이고, 콘솔은 그 projection 의
 * 독자일 뿐이다.
 *
 * <p>JPA 엔티티를 두지 않는 이유: {@code ddl-auto: validate} 환경에서 원장 5테이블을 매핑하면
 * 소유하지도 않는 스키마에 이 앱의 기동이 묶인다. 읽기 집계 하나라 JdbcTemplate 이면 충분하다.
 *
 * <p>인터페이스로 두는 것은 standalone 컨트롤러 테스트가 손 페이크로 대체하기 위함이다
 * (레포 hand-fake 관례, Mockito 미도입).
 */
public interface PipelineStatusRepository {

	/** 가장 최근에 계획된 파이프라인 런의 상태. 원장에 런이 하나도 없으면 empty. */
	Optional<PipelineRunStatus> latestRun();

	/**
	 * 런 하나와 그 런의 기대 작업들.
	 *
	 * @param runKey             슬롯 멱등키(예: {@code etf-daily:2026-07-27T15:40}) — 화면의 "언제 런"
	 * @param launchStatus       Planner 의 SFN 기동 결과(LAUNCHED·LAUNCH_FAILED·…). <b>기동 실패는
	 *                           orchestration 이 영영 null 이라</b> 이 축이 없으면 "아예 시작 못 함"이
	 *                           화면에서 "표시할 상태 없음"으로 사라진다 — 원장이 답하려는 바로 그
	 *                           질문("원래 실행돼야 했는데 시작되지 않은 것")이다
	 * @param orchestrationStatus SFN 실행 귀결(RUNNING·SUCCEEDED·FAILED·…). null 이면 아직 미확인
	 * @param tradingDate        대상 거래일. 비거래일 SKIP 판정의 근거라 화면에 함께 낸다
	 */
	record PipelineRunStatus(String runKey, String launchStatus, String orchestrationStatus,
			LocalDate tradingDate, List<TaskStatus> tasks) {
	}

	/**
	 * 기대 작업 한 건의 관측 상태.
	 *
	 * <p>{@code planStatus}(DUE·SKIPPED)와 {@code outcome}(PENDING·FULFILLED·FAILED·BLOCKED·MISSED)은
	 * <b>다른 축</b>이라 합치지 않는다 — "원래 할 일이었나"와 "그래서 어떻게 됐나"는 운영자가
	 * 각각 알아야 하고, 하나로 뭉개면 SKIPPED(휴장이라 안 함)와 FULFILLED(해서 됐음)가 같은
	 * 초록이 된다.
	 *
	 * <p>{@code dataStatus}(UNKNOWN·VALID·VALID_EMPTY·INCOMPLETE·INVALID)는 실행 성패와 <b>또 다른
	 * 축</b>이다 — 실행이 성공(FULFILLED)해도 산출 데이터는 INCOMPLETE 일 수 있다. 이 축을 빼면
	 * 데이터가 불완전한 작업이 화면에서 온전히 초록으로 보인다(Rule 12).
	 *
	 * <p>{@code recordsOut}·{@code failedRecords}는 <b>null 이 정상값</b>이다(ALPHA-182) — 신호가
	 * 없거나 못 믿을 값이면 0 으로 메우지 않는다. 0 으로 내리면 화면에서 "0건 처리"와 "모름"이
	 * 구분되지 않는다.
	 */
	record TaskStatus(String stage, String taskKey, String dataset, String planStatus,
			String outcome, String dataStatus, Long recordsOut, Long failedRecords,
			OffsetDateTime lastFinishedAt) {
	}
}
