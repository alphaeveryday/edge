package com.edge.tenantconsole.model;

import java.util.List;

/**
 * 검수 목록 항목(ALPHA-436) — 항목 + 파생 검수 사유. 사유는 screening_check 의
 * result='REVIEW' 행을 rule_type 으로 해석한 값이다(analysis_item 에 사유 컬럼을
 * 중복하지 않는다 — DDL 규약).
 *
 * gateChecks 는 룰 무관 판정(게이트·스위치) 행이다(ALPHA-774). reviewReasons 는 이들을
 * 전부 "자동 제공 기준 미충족" 하나로 뭉치는데, 상세는 판정 당시 기준으로 구체 문구를
 * 만든다 — 목록만 뭉뚱그리면 같은 항목의 사유가 두 화면에서 달라진다. 문구는 화면이
 * 만들고(어휘는 UI SSOT), 여기서는 그 재료(실측·판정 당시 기준)만 보낸다.
 */
public record ReviewListEntry(ReviewItem item, List<String> reviewReasons,
		List<GateCheck> gateChecks) {

	/** 룰 무관 REVIEW 한 행 — matchedText 는 실측, min* 는 판정 당시 기준. */
	public record GateCheck(String matchedText, Integer minSourceCount, String minConfidence) {
	}
}
