package com.edge.app.application.explore.provided;

import com.edge.app.application.etf.provided.EtfSummaryResponse;

import java.util.List;

public record RankRowResponse(EtfSummaryResponse etf, int rank, String title, List<String> chips, boolean ready) {
}
