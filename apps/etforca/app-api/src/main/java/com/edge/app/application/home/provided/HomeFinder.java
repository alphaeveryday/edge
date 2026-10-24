package com.edge.app.application.home.provided;

import com.edge.app.application.member.provided.AppPrincipal;

import java.util.List;

/**
 * 홈 브리프와 시장 지수 조회
 */
public interface HomeFinder {
    HomeBriefResponse brief(AppPrincipal principal, String group);

    List<MarketIndexResponse> indices();
}
