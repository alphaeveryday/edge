package com.edge.app.theme.service;

import com.edge.app.etf.entity.Dir;
import com.edge.app.etf.service.EtfService;
import com.edge.app.theme.dto.ThemeDetailResponse;
import com.edge.app.theme.dto.ThemeFeedItemResponse;
import com.edge.app.theme.dto.ThemeResponse;
import com.edge.app.theme.dto.ThemeSheetResponse;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.List;

/** 스텁. */
@Service
public class ThemeService {
    private static final Instant AT = Instant.parse("2026-01-01T00:00:00Z");

    public List<ThemeResponse> list() {
        return List.of(new ThemeResponse("key", "label", "industry", false));
    }

    public List<ThemeFeedItemResponse> feed(String dir) {
        return List.of(new ThemeFeedItemResponse("key", 0, "headline", Dir.NEUTRAL));
    }

    public ThemeSheetResponse sheet(String key) {
        return new ThemeSheetResponse(key, "title", "why", List.of(new ThemeSheetResponse.Row(EtfService.SUMMARY, "tag")));
    }

    public ThemeDetailResponse detail(String key) {
        var metric = new ThemeDetailResponse.Metric("name", "now", Dir.NEUTRAL, List.of(0.0), 0, List.of("x"), "refLabel", "state");
        return new ThemeDetailResponse(key, "headline", List.of(new ThemeDetailResponse.Stock("name", "etfs")), "intro", AT,
                "countLabel", "todayLine", "todayEffect", "importantLead", "importantWhy", metric, "thesis", "surface",
                "structure", "structureWhy", "soWhat");
    }
}
