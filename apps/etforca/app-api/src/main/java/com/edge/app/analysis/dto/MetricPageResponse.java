package com.edge.app.analysis.dto;

import com.edge.app.analysis.entity.Axis;
import com.edge.app.etf.entity.Dir;

import java.util.List;

public record MetricPageResponse(Axis axis, Dir dir, String verdict, List<Tile> tiles, boolean hasDetail) {
    public record Tile(String label, String value, Dir dir, String note, boolean wide) {
    }
}
