package com.edge.app.etf.dto;

import com.edge.app.etf.entity.Dir;

import java.util.List;

public record EtfDetailResponse(Insight insight, List<HeatCell> stocks, List<HeatCell> themes,
        List<HoldingRow> holdings, List<HoldingRow> themeRows, int stockCount, List<Info> info, String blurb) {
    public record Insight(Dir dir, String text) {
    }

    public record HeatCell(String name, double weight, double changePct, Dir dir) {
    }

    public record HoldingRow(String name, double weight, Dir dir, String desc) {
    }

    public record Info(String k, String v) {
    }
}
