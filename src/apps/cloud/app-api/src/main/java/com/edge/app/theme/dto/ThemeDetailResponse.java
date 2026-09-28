package com.edge.app.theme.dto;

import com.edge.app.etf.entity.Dir;

import java.time.Instant;
import java.util.List;

public record ThemeDetailResponse(String key, String headline, List<Stock> stocks, String intro, Instant updated,
        String countLabel, String todayLine, String todayEffect, String importantLead, String importantWhy,
        Metric metric, String thesis, String surface, String structure, String structureWhy, String soWhat) {
    public record Stock(String name, String etfs) {
    }

    public record Metric(String name, String now, Dir dir, List<Double> vals, double thresh, List<String> xLabels,
            String refLabel, String state) {
    }
}
