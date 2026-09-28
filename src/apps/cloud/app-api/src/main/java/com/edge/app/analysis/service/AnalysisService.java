package com.edge.app.analysis.service;

import com.edge.app.analysis.dto.DailyAnalysisResponse;
import com.edge.app.analysis.dto.FactorPageResponse;
import com.edge.app.analysis.dto.MetricPageResponse;
import com.edge.app.analysis.entity.Axis;
import com.edge.app.etf.entity.Dir;
import com.edge.app.etf.entity.Signal;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.util.List;

/** 스텁. */
@Service
public class AnalysisService {
    private static final LocalDate DATE = LocalDate.of(2026, 1, 1);

    public DailyAnalysisResponse daily(String code, LocalDate date) {
        LocalDate day = date == null ? DATE : date;
        return new DailyAnalysisResponse(day, List.of(new DailyAnalysisResponse.DailyDate(day, "w", "d", true)),
                "headTitle", "question", "dateline", Signal.NEUTRAL, Signal.NEUTRAL, "synth",
                List.of(new DailyAnalysisResponse.AxisRead(Axis.ISSUE, Dir.NEUTRAL, "summary", false)), "title",
                "todayDate", List.of("today"), List.of(new DailyAnalysisResponse.Arg("1", "claim", List.of("body"))),
                "closingTitle", List.of("neg"), List.of("pos"), "close", new DailyAnalysisResponse.Next("000000", "name"));
    }

    public FactorPageResponse factor(String code, Axis axis) {
        return new FactorPageResponse(axis, Dir.NEUTRAL, "headline",
                List.of(new FactorPageResponse.Event(Dir.NEUTRAL, "k", "body")),
                List.of(new FactorPageResponse.ChartInd(Dir.NEUTRAL, "name", "d")), List.of("judg"));
    }

    public MetricPageResponse metric(String code, Axis axis) {
        return new MetricPageResponse(axis, Dir.NEUTRAL, "verdict",
                List.of(new MetricPageResponse.Tile("label", "value", Dir.NEUTRAL, "note", false)), false);
    }
}
