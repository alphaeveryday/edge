package com.edge.app.application.analysis.provided;

import com.edge.app.domain.analysis.Axis;
import com.edge.app.domain.etf.Dir;

import java.util.List;

public record MetricPageResponse(Axis axis, Dir dir, String verdict, List<Tile> tiles, boolean hasDetail) {
    public record Tile(String label, String value, Dir dir, String note, boolean wide) {
    }
}
