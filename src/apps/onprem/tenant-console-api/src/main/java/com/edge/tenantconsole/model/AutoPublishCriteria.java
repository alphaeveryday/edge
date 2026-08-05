package com.edge.tenantconsole.model;

/**
 * 자동 제공 기준(ALPHA-438) — policy_version 의 min_source_count·min_confidence 투영.
 * autoPublishEnabled 는 자동 제공 스위치(ALPHA-756 에서 조작 수단 신설). 꺼져 있으면
 * 어디에도 걸리지 않은 설명까지 검수로 가므로, 기준만 보여주는 화면은 실제 처리와
 * 어긋난다 — 그래서 기준과 함께 실어 보낸다.
 * minSources 는 nullable 이다: DDL 이 NULL 을 "출처 수 조건 없음"으로 정의하고 평가기도
 * null 이면 게이트를 건너뛴다. 기본값으로 채우면 조건이 없는 정책을 있는 것처럼 보여준다
 * (확신도와 같은 처리). 활성 버전이 없을 때의 온보딩 기반값 2 는 loadBase 가 준다.
 */
public record AutoPublishCriteria(boolean published, boolean autoPublishEnabled,
		Integer minSources, String minConfidence) {
}
