package com.edge.app.home.dto;

import com.edge.app.etf.dto.EtfSummaryResponse;
import com.edge.app.etf.entity.Signal;
import com.edge.app.watch.dto.WatchGroupResponse;

import java.time.Instant;
import java.util.List;

public record HomeBriefResponse(Instant asOf, List<WatchGroupResponse> groups, String group, Signal band,
        double score, double changePct, List<EtfSummaryResponse> etfs) {
}
