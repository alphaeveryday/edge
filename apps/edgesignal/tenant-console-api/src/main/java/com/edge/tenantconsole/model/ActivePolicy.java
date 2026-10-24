package com.edge.tenantconsole.model;

import java.util.List;

/**
 * 활성 정책 스냅샷(ALPHA-762) — 기준(게이트)과 룰을 **한 번에** 낸다.
 *
 * 둘을 따로 조회하면 그 사이 다른 세션의 발행으로 서로 다른 버전이 한 화면에 섞이는데,
 * 응답에 버전 표시가 없어 화면이 섞였다는 사실조차 모른다(정책은 매 변경이 새 버전,
 * ADR-0018). 감지 로직을 얹는 대신 한 트랜잭션 스냅샷으로 경합 자체를 없앤다.
 *
 * published=false 는 활성 버전이 아직 없다는 뜻이다 — 그 구간엔 screening-worker 가
 * "정책 부재 = 진행 중단"으로 NEW 를 판정하지 않으므로(BundleScreener) 화면이 구분해야 한다.
 * 그때 versionNo 는 null 이고 나머지 값은 첫 발행에 쓰일 기반값이다.
 */
public record ActivePolicy(boolean published, Integer versionNo, boolean autoPublishEnabled,
		Integer minSources, String minConfidence, List<ScreeningRule> rules) {
}
