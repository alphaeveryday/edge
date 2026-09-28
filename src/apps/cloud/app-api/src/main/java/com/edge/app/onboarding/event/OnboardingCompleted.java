package com.edge.app.onboarding.event;

import java.util.List;

/** 고른 ETF 를 기본 관심 그룹에 넣는 것은 watch 도메인의 쓰기라 이벤트로 넘긴다. 같은 트랜잭션에서 처리된다. */
public record OnboardingCompleted(long principalId, List<String> etfs) {
}
