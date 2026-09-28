package com.edge.app.etf.service;

import com.edge.app.etf.dto.ChartResponse;
import com.edge.app.etf.dto.EtfDetailResponse;
import com.edge.app.etf.dto.EtfSummaryResponse;
import com.edge.app.etf.dto.MoveResponse;
import com.edge.app.etf.entity.Dir;
import com.edge.app.etf.entity.Signal;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.List;

/** 스텁. */
@Service
public class EtfService {
    public static final EtfSummaryResponse SUMMARY =
            new EtfSummaryResponse("000000", "name", "theme", 0, 0, Signal.NEUTRAL, false, "sub");
    private static final Instant AT = Instant.parse("2026-01-01T00:00:00Z");

    public List<EtfSummaryResponse> list(String q) {
        return List.of(SUMMARY);
    }

    public EtfSummaryResponse get(String code) {
        return SUMMARY;
    }

    public ChartResponse chart(String code, String range) {
        return new ChartResponse(range, List.of(new ChartResponse.Candle(0, 0, 0, 0)), List.of(0.0), List.of(0.0), List.of("axis"));
    }

    public MoveResponse move(String code) {
        return new MoveResponse(AT, "text", "foot", "sheetTitle",
                List.of(new MoveResponse.Group("head", List.of(new MoveResponse.Item(Dir.NEUTRAL, "t", "sub")))));
    }

    public EtfDetailResponse detail(String code) {
        var cell = new EtfDetailResponse.HeatCell("name", 0, 0, Dir.NEUTRAL);
        var row = new EtfDetailResponse.HoldingRow("name", 0, Dir.NEUTRAL, "desc");
        return new EtfDetailResponse(new EtfDetailResponse.Insight(Dir.NEUTRAL, "text"), List.of(cell), List.of(cell),
                List.of(row), List.of(row), 0, List.of(new EtfDetailResponse.Info("k", "v")), "blurb");
    }
}
