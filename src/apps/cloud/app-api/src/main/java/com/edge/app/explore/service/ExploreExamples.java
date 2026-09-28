package com.edge.app.explore.service;

import com.edge.app.etf.service.EtfExamples;
import com.edge.app.explore.dto.RankRowResponse;

import java.util.List;

/** 스텁 고정값. 실구현 때 삭제. */
final class ExploreExamples {
    private ExploreExamples() {
    }

    static RankRowResponse rankRow() {
        return new RankRowResponse(EtfExamples.summary(), 1, "title", List.of("chip"), false);
    }
}
