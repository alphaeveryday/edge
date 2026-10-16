package com.edge.app.application.analysis.provided;

import com.edge.app.domain.analysis.Axis;
import com.edge.app.domain.etf.Dir;
import com.edge.app.domain.etf.Signal;

import java.time.LocalDate;
import java.util.List;

public record DailyAnalysisResponse(LocalDate date, List<DailyDate> dates, String headTitle, String question,
        String dateline, Signal now, Signal prev, String synth, List<AxisRead> axes, String title, String todayDate,
        List<String> today, List<Arg> args, String closingTitle, List<String> neg, List<String> pos, String close,
        Next next, LocalDate prevWeek, LocalDate nextWeek) {
    public record DailyDate(LocalDate key, String w, String d, boolean hasDaily) {
    }

    public record AxisRead(Axis axis, Dir dir, String summary, boolean hasPage) {
    }

    public record Arg(String no, String claim, List<String> body) {
    }

    public record Next(String code, String name) {
    }
}
