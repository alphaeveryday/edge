package com.edge.app.application.analysis.provided;

import com.edge.app.domain.analysis.Axis;

import java.time.LocalDate;

/**
 * 일간 분석과 축별 상세 조회
 */
public interface AnalysisFinder {
    DailyAnalysisResponse daily(String code, LocalDate date, LocalDate week);

    FactorPageResponse factor(String code, Axis axis);

    MetricPageResponse metric(String code, Axis axis);
}
