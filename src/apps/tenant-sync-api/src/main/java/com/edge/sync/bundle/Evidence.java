package com.edge.sync.bundle;

import java.util.Map;
import java.util.UUID;

/** 설명 근거. payload 형상은 kind(NEWS·DISCLOSURE·PRICE·FLOW)별 — 계약의 [합의 필요] 항목이라 Map 으로 둔다. */
public record Evidence(
		UUID evidenceId,
		String kind,
		Map<String, Object> payload
) {
}
