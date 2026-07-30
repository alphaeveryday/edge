package com.edge.tenantconsole.model;

import java.util.List;

/**
 * 검수 목록 항목(ALPHA-436) — 항목 + 파생 검수 사유. 사유는 screening_check 의
 * result='REVIEW' 행을 rule_type 으로 해석한 값이다(analysis_item 에 사유 컬럼을
 * 중복하지 않는다 — DDL 규약).
 */
public record ReviewListEntry(ReviewItem item, List<String> reviewReasons) {
}
