package com.edge.tenantconsole.model;

import java.time.OffsetDateTime;

/**
 * 반입 집계 조회 결과(ALPHA-607) — analysis_item 한 번의 스캔으로 오늘 반입 수와 최근
 * 반입 시각을 함께 얻는다(ExplanationLedgerRepository.summarizeFeed). state 판정은
 * 서비스가 lastReceivedAt 으로 한다.
 */
public record FeedStatusAggregate(long todayReceived, OffsetDateTime lastReceivedAt) {
}
