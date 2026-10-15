package com.edge.app.explore.dto;

import com.edge.app.etf.dto.EtfSummaryResponse;

import java.util.List;

public record RankRowResponse(EtfSummaryResponse etf, int rank, String title, List<String> chips, boolean ready) {
}
