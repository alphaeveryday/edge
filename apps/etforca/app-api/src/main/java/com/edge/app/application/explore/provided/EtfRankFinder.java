package com.edge.app.application.explore.provided;

import java.util.List;

/**
 * AI 선정 ETF 순위 조회
 */
public interface EtfRankFinder {
    List<RankRowResponse> rank();
}
