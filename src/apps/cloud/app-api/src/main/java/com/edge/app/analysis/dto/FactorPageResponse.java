package com.edge.app.analysis.dto;

import com.edge.app.analysis.entity.Axis;
import com.edge.app.etf.entity.Dir;

import java.util.List;

public record FactorPageResponse(Axis axis, Dir dir, String headline, List<Event> events, List<ChartInd> chartInds,
        List<String> judg) {
    public record Event(Dir dir, String k, String body) {
    }

    public record ChartInd(Dir dir, String name, String d) {
    }
}
