package com.edge.tenantconsole.model;

import java.time.OffsetDateTime;
import java.util.List;

/**
 * 가격 변동 설명 도메인 표현(ALPHA-607 실전환) — 서비스가 원장(analysis_item)에
 * 파생 검수 사유·게시 문구를 결합해 만들고 dto(ExplanationResponse)가 UI 어휘로
 * 번역한다(엔티티는 영속 계층에 머문다). 시각은 원값(OffsetDateTime)으로 두고 표시
 * 포맷은 dto 가 KST 로 맞춘다.
 *
 * <p>UI 계약 필드 중 market·direction·changePct 는 온프렘 원장에 없어(경계면 확장
 * ALPHA-497 이연 — event-bundle-schema.md) 이 표현에도 없다. 축소 계약(사용자 결정
 * 2026-07-29)에 따라 화면에서도 제거됐고, ALPHA-497 materialization 후 복원한다.
 *
 * <p>explanationAsOf(스냅샷 기준시각)·serving(노출 head 여부)은 다스냅샷 공존(ALPHA-743)
 * 판별 축(ALPHA-744) — serving 은 서버가 서빙 술어로 판정한 값이다(UI 파생 금지).
 */
public record Explanation(
		String id,
		String name,
		String code,
		String status,
		String confidence,
		String reviewReason,
		OffsetDateTime receivedAt,
		OffsetDateTime explanationAsOf,
		boolean serving,
		List<Evidence> evidence,
		String original,
		String finalText
) {
	/**
	 * 근거 문서 원값 — kind·published_at 은 dto 가 UI 어휘(공시/뉴스)·표시 시각으로 번역한다.
	 * sourceUri 는 원문 링크(ALPHA-739) — 계약상 optional 이라 결측이면 null(EOD 뉴스 구멍,
	 * ALPHA-740).
	 */
	public record Evidence(String kind, String title, String source, OffsetDateTime publishedAt,
			String sourceUri) {
	}
}
