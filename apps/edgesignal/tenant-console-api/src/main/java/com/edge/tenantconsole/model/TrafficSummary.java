package com.edge.tenantconsole.model;

/**
 * 서빙 트래픽 집계(ALPHA-128) — serving_request_metric 윈도 집계 결과.
 * 총량·에러 수만 담고 에러율은 소비자(UI)가 파생한다 — 반올림 정책을 서버에 박지 않는다.
 */
public record TrafficSummary(long totalRequests, long errorRequests) {
}
