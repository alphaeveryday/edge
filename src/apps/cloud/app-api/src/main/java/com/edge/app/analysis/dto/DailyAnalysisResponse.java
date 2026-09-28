package com.edge.app.analysis.dto;

import com.edge.app.analysis.entity.Axis;
import com.edge.app.etf.entity.Dir;
import com.edge.app.etf.entity.Signal;

import java.time.LocalDate;
import java.util.List;

public record DailyAnalysisResponse(LocalDate date, List<DailyDate> dates, String headTitle, String question,
        String dateline, Signal now, Signal prev, String synth, List<AxisRead> axes, String title, String todayDate,
        List<String> today, List<Arg> args, String closingTitle, List<String> neg, List<String> pos, String close,
        Next next) {
    public record DailyDate(LocalDate key, String w, String d, boolean hasDaily) {
    }

    public record AxisRead(Axis axis, Dir dir, String summary, boolean hasPage) {
    }

    public record Arg(String no, String claim, List<String> body) {
    }

    public record Next(String code, String name) {
    }
}
