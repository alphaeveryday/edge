package com.edge.app.analysis.service;

import com.edge.app.analysis.dto.DailyAnalysisResponse;
import com.edge.app.analysis.dto.FactorPageResponse;
import com.edge.app.analysis.dto.MetricPageResponse;
import com.edge.app.analysis.entity.Axis;
import org.springframework.stereotype.Service;

import java.time.LocalDate;

/** 스텁. */
@Service
public class AnalysisService {
    public DailyAnalysisResponse daily(String code, LocalDate date) {
        return AnalysisExamples.daily(date);
    }

    public FactorPageResponse factor(String code, Axis axis) {
        return AnalysisExamples.factor(axis);
    }

    public MetricPageResponse metric(String code, Axis axis) {
        return AnalysisExamples.metric(axis);
    }
}
