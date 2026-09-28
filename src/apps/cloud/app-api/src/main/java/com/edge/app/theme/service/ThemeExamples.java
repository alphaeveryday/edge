package com.edge.app.theme.service;

import com.edge.app.etf.entity.Dir;
import com.edge.app.etf.service.EtfExamples;
import com.edge.app.theme.dto.ThemeDetailResponse;
import com.edge.app.theme.dto.ThemeFeedItemResponse;
import com.edge.app.theme.dto.ThemeResponse;
import com.edge.app.theme.dto.ThemeSheetResponse;

import java.time.Instant;
import java.util.List;

/** 스텁 고정값. 실구현 때 삭제. */
final class ThemeExamples {
    private static final Instant AT = Instant.parse("2026-01-01T00:00:00Z");

    private ThemeExamples() {
    }

    static ThemeResponse theme() {
        return new ThemeResponse("key", "label", "industry", false);
    }

    static ThemeFeedItemResponse feedItem() {
        return new ThemeFeedItemResponse("key", 0, "headline", Dir.NEUTRAL);
    }

    static ThemeSheetResponse sheet(String key) {
        return new ThemeSheetResponse(key, "title", "why", List.of(new ThemeSheetResponse.Row(EtfExamples.summary(), "tag")));
    }

    static ThemeDetailResponse detail(String key) {
        var metric = new ThemeDetailResponse.Metric("name", "now", Dir.NEUTRAL, List.of(0.0), 0, List.of("x"), "refLabel", "state");
        return new ThemeDetailResponse(key, "headline", List.of(new ThemeDetailResponse.Stock("name", "etfs")), "intro", AT,
                "countLabel", "todayLine", "todayEffect", "importantLead", "importantWhy", metric, "thesis", "surface",
                "structure", "structureWhy", "soWhat");
    }
}
