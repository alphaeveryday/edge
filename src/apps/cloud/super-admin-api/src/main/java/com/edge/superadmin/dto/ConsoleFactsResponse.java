package com.edge.superadmin.dto;

import com.edge.superadmin.repository.ConsoleFactsRepository.RunRow;
import com.fasterxml.jackson.annotation.JsonInclude;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

/**
 * 콘솔 규칙 엔진의 사실 응답(ALPHA-738 · docs/contracts/console-facts-api.md).
 *
 * <p>필드명은 UI 타입과 같은 camelCase(기존 콘솔 API 관례). 원장 record 와 형식이 같아도 와이어
 * 형은 별도 타입으로 둔다.
 *
 * <p><b>부재를 싣는 규약이 이 타입의 전부다.</b> 실측 0 은 {@code 0}, 집계 없음·관측 불가는
 * {@code null}, <b>계측 없음은 필드 자체를 두지 않는다</b>. 그래서 클래스 단위
 * {@code @JsonInclude} 를 걸지 않는다 — NON_NULL 을 위에 걸면 "집계 없음(null)"이 조용히
 * "계측 없음(필드 부재)"으로 바뀌어, 콘솔이 없애려는 칸 혼동을 서버가 다시 만든다.
 *
 * <p>지금 내는 것은 <b>조회 창 + 런 축</b>이다. 작업·데이터셋·산출·경계는 뒤따르는 조각이
 * 하나씩 더하고, <b>붙기 전까지 그 필드는 응답에 아예 없다</b> — 빈 배열이 아니다. 같은 규약이다:
 * 빈 배열은 "봤는데 없었다"이고 필드 부재는 "아직 안 본다"라, 규칙 층이 그 둘을 다르게 센다.
 *
 * <p>표시 문자열을 만들지 않는다 — 건수·시각·판정 코드를 raw 로 내리고 포맷은 UI 소관이다
 * ({@link SourceReportResponse} 와 같은 규약).
 */
public record ConsoleFactsResponse(List<RunResponse> runs, MetaResponse meta) {

	/**
	 * 런 하나. {@code id} 는 {@code run_key} 다 — 사건 식별자의 대상 축이라 내부 id 를 쓰면
	 * 다른 축과 조인이 끊긴다.
	 *
	 * <p>시각·날짜는 ISO 문자열로 내린다. 표시 형식을 만들지 않는 것이 이 응답의 규약이다.
	 *
	 * <p>{@code planned}·{@code noRunRow} 는 <b>런 행이 없는 계획 슬롯</b>에만 실린다. 실재 런에
	 * 대해 "스케줄 상 있어야 할 슬롯인가"를 답하는 계측이 없어서 {@code null} 을 내보내지 않고
	 * <b>필드를 뺀다</b> — {@code false} 로 채우면 모름이 "계획된 적 없다"는 단정으로 뒤집힌다.
	 * 여기가 이 응답에서 {@code @JsonInclude} 를 <b>필드 단위로</b> 거는 유일한 자리다.
	 */
	public record RunResponse(String id, String lane, String tradingDate, String ledgerStatus,
			String ledgerUpdated, String deadline,
			@JsonInclude(JsonInclude.Include.NON_NULL) Boolean planned,
			@JsonInclude(JsonInclude.Include.NON_NULL) Boolean noRunRow) {

		public static RunResponse from(RunRow r) {
			return new RunResponse(r.runKey(), r.lane(), iso(r.tradingDate()), r.ledgerStatus(),
					iso(r.ledgerUpdated()), iso(r.deadline()), r.planned(), r.noRunRow());
		}
	}

	/** {@code today} 는 실제로 조회한 날 — 요청이 date 를 생략했을 때 무엇을 본 응답인가.
	 *  <b>거래일이라는 보장은 없다</b>(계획만 있던 날·원장이 빈 경우의 KST 오늘). */
	public record MetaResponse(String db, String today) {
	}

	private static String iso(OffsetDateTime at) {
		return at == null ? null : at.toString();
	}

	private static String iso(LocalDate date) {
		return date == null ? null : date.toString();
	}
}
