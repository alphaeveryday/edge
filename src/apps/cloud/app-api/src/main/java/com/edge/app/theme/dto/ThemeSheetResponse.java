package com.edge.app.theme.dto;

import com.edge.app.etf.dto.EtfSummaryResponse;

import java.util.List;

public record ThemeSheetResponse(String theme, String title, String why, List<Row> rows) {
    public record Row(EtfSummaryResponse etf, String tag) {
    }
}
