package com.edge.app.application.etf.provided;

import com.edge.app.domain.etf.ChartRange;

import java.util.List;

/**
 * ETF 조회
 */
public interface EtfFinder {
    List<EtfSummaryResponse> list(String q);

    EtfSummaryResponse get(String code);

    ChartResponse chart(String code, ChartRange range);

    MoveResponse move(String code);

    EtfDetailResponse detail(String code);
}
