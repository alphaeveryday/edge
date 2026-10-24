package com.edge.app.application.home.provided;

import com.edge.app.application.etf.provided.EtfSummaryResponse;
import com.edge.app.application.watch.provided.WatchGroupResponse;
import com.edge.app.domain.etf.Signal;

import java.time.Instant;
import java.util.List;

public record HomeBriefResponse(Instant asOf, List<WatchGroupResponse> groups, String group, Signal band,
        double score, double changePct, List<EtfSummaryResponse> etfs) {
}
