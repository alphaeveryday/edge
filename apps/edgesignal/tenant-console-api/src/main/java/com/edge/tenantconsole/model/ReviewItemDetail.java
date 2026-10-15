package com.edge.tenantconsole.model;

import tools.jackson.databind.JsonNode;

import java.time.OffsetDateTime;
import java.util.List;

/**
 * 검수 상세(ALPHA-436, 구 439 흡수) — 항목 본체 + 근거 문서(evidences JSONB) +
 * 파생 검수 사유 + 컴플라이언스 검사 결과(screening_check 전 행) + 상태 변경 이력.
 * 감사 이력은 별도 메뉴가 아니라 이 상세로 확인한다(콘솔 IA — 고객 단위 노출 이력은 ADR-0053 으로 폐지).
 */
public record ReviewItemDetail(ReviewItem item, JsonNode evidences, List<String> reviewReasons,
		List<ScreeningCheckView> checks, List<StatusChange> history) {

	/**
	 * 검사 결과 한 행 — ruleType 은 룰 무관 판정(자동 제공 스위치·게이트)이면 null.
	 *
	 * policyVersionNo·minSourceCount·minConfidence 는 **판정 당시** 값이다(ALPHA-774).
	 * 검수 사유를 "운영자가 설정한 기준의 문구"로 쓰려면 기준이 필요한데, matchedText 는
	 * 실측값이라(`source_events=1`) 기준을 담지 않는다. 기준의 출처는 언제나 기록된
	 * 정책 버전이다 — 현재 설정으로 과거 판정을 라벨링하면 감사 재현이 어긋난다.
	 * 버전이 삭제·미조회면 세 값 모두 null 이고, 화면은 기준 없이 실측만 보여준다.
	 */
	public record ScreeningCheckView(String result, String ruleType, String matchedText,
			OffsetDateTime checkedAt, Integer policyVersionNo, Integer minSourceCount,
			String minConfidence) {
	}

	/** 상태 전이 한 건 — actorName 은 SYSTEM 전이(자동 분기·Cloud 이벤트)면 null. */
	public record StatusChange(String fromStatus, String toStatus, String actorType,
			String actorName, String reason, OffsetDateTime occurredAt) {
	}
}
