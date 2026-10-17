package com.edge.app.domain.onboarding;

import java.util.List;

/**
 * 온보딩 완료 이벤트
 * 기본 그룹 추가의 watch 도메인 소관
 * 같은 트랜잭션 처리
 */
public record OnboardingCompleted(long principalId, List<String> etfs) {
}
