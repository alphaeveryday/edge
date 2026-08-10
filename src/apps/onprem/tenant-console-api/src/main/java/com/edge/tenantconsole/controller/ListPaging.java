package com.edge.tenantconsole.controller;

/**
 * 목록 페이지네이션 파라미터 정규화(ALPHA-914) — 무한 스크롤 소비자는 50개 단위로
 * 요청하고, 범위 밖 값은 거부 대신 안전 범위로 눌러 담는다(콘솔 내부 표면이라
 * 오타·조작 입력에 4xx 어휘를 늘릴 실익이 없다).
 */
final class ListPaging {

	static final int MAX_LIMIT = 100;

	private ListPaging() {
	}

	static int clampLimit(int limit) {
		return Math.min(Math.max(limit, 1), MAX_LIMIT);
	}

	static int clampOffset(int offset) {
		return Math.max(offset, 0);
	}
}
