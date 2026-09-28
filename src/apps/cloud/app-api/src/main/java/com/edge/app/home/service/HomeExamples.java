package com.edge.app.home.service;

import com.edge.app.etf.entity.Signal;
import com.edge.app.etf.service.EtfExamples;
import com.edge.app.home.dto.HomeBriefResponse;
import com.edge.app.watch.dto.WatchGroupResponse;

import java.time.Instant;
import java.util.List;

/** 스텁 고정값. 실구현 때 삭제. */
final class HomeExamples {
    private static final Instant AT = Instant.parse("2026-01-01T00:00:00Z");

    private HomeExamples() {
    }

    static HomeBriefResponse brief(String group) {
        var base = new WatchGroupResponse("base", "label", 1);
        return new HomeBriefResponse(AT, List.of(base), group == null ? base.key() : group, Signal.NEUTRAL, 0,
                List.of(EtfExamples.summary()));
    }
}
