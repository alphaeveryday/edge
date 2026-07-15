package com.edge.sync.bundle;

import java.math.BigDecimal;
import java.util.List;
import java.util.UUID;

/** AI 설명 후보 — "공개 정보 기반 변동 요인 후보" 표현 원칙(docs/writing-rules.md)의 데이터 단위. */
public record ExplanationCandidate(
		UUID candidateId,
		String analysisType,
		String body,
		BigDecimal confidence,
		List<String> counterFactors
) {
}
