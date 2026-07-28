package com.edge.superadmin.dto;

import com.edge.superadmin.repository.PipelineStatusRepository.GridCell;
import com.edge.superadmin.repository.PipelineStatusRepository.GridSlot;

import java.util.List;

/**
 * 실행 격자 응답 — 최근 N일의 슬롯(런)과 각 슬롯의 기대 작업(ALPHA-594).
 *
 * <p>필드는 super-admin-ui sources 타입과 동일한 camelCase. 피벗(슬롯×작업 표)은 UI 가 한다 —
 * 서버가 행/열을 미리 짜면 "행 축을 무엇으로 접을 것인가"(IA 미확정)가 와이어 계약에 굳는다.
 *
 * <p>{@code slots} 는 계획 시각 오름차순이고 <b>배열 순서가 곧 표시 순서</b>다 — 슬롯 정렬
 * 기준(created_at)은 원장 내부 값이라 와이어에 싣지 않는다. 창 안에 런이 없으면 빈 배열
 * (초기 환경의 정상 상태 — 에러가 아니다).
 *
 * <p>표시 문자열을 만들지 않는 것, 건수 null(모름 ≠ 0) 계약은 {@link SourceReportResponse} 와
 * 같다.
 */
public record SourceGridResponse(int days, List<SlotResponse> slots) {

	public record SlotResponse(String runKey, String launchStatus, String orchestrationStatus,
			String tradingDate, List<CellResponse> tasks) {

		public static SlotResponse from(GridSlot slot) {
			return new SlotResponse(slot.runKey(), slot.launchStatus(),
					slot.orchestrationStatus(),
					slot.tradingDate() == null ? null : slot.tradingDate().toString(),
					slot.tasks().stream().map(CellResponse::from).toList());
		}
	}

	public record CellResponse(String stage, String taskKey, String planStatus, String outcome,
			String dataStatus, Long recordsOut, Long failedRecords, String skipReason,
			String outcomeReason) {

		public static CellResponse from(GridCell cell) {
			return new CellResponse(cell.stage(), cell.taskKey(), cell.planStatus(),
					cell.outcome(), cell.dataStatus(), cell.recordsOut(), cell.failedRecords(),
					cell.skipReason(), cell.outcomeReason());
		}
	}

	public static SourceGridResponse from(int days, List<GridSlot> slots) {
		return new SourceGridResponse(days, slots.stream().map(SlotResponse::from).toList());
	}
}
