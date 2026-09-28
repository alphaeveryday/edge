package com.edge.app.etf.dto;

import com.edge.app.etf.entity.Signal;

public record EtfSummaryResponse(String code, String name, String theme, double price, double changePct,
        Signal signal, boolean hot, String sub) {
}
