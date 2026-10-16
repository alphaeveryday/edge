package com.edge.app.application.analysis.provided;

import com.edge.app.domain.analysis.Axis;
import com.edge.app.domain.etf.Dir;

import java.util.List;

public record FactorPageResponse(Axis axis, Dir dir, String headline, List<Event> events, List<ChartInd> chartInds,
        List<String> judg) {
    public record Event(Dir dir, String k, String body) {
    }

    public record ChartInd(Dir dir, String name, String d) {
    }
}
