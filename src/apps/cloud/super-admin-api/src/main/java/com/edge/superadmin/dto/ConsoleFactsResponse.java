package com.edge.superadmin.dto;

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
 * <p>이 조각은 <b>조회 창만</b> 낸다. 사실 축(런·작업·데이터셋·산출·경계)은 뒤따르는 조각이
 * 하나씩 더하고, <b>붙기 전까지 그 필드는 응답에 아예 없다</b> — 빈 배열이 아니다. 같은 규약이다:
 * 빈 배열은 "봤는데 없었다"이고 필드 부재는 "아직 안 본다"라, 규칙 층이 그 둘을 다르게 센다.
 *
 * <p>표시 문자열을 만들지 않는다 — 건수·시각·판정 코드를 raw 로 내리고 포맷은 UI 소관이다
 * ({@link SourceReportResponse} 와 같은 규약).
 */
public record ConsoleFactsResponse(MetaResponse meta) {

	/** {@code today} 는 실제로 조회한 날 — 요청이 date 를 생략했을 때 무엇을 본 응답인가.
	 *  <b>거래일이라는 보장은 없다</b>(계획만 있던 날·원장이 빈 경우의 KST 오늘). */
	public record MetaResponse(String db, String today) {
	}
}
