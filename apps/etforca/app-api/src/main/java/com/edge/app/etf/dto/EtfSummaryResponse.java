package com.edge.app.etf.dto;

import com.edge.app.etf.entity.Signal;
import com.edge.app.etf.repository.EtfRepository;

public record EtfSummaryResponse(String code, String name, String theme, double price, double changePct,
        Signal signal, boolean hot, String sub) {
    public static EtfSummaryResponse from(EtfRepository.SummaryRow row) {
        return new EtfSummaryResponse(row.getCode(), row.getName(), row.getThemeKey(), row.getPrice().doubleValue(),
                row.getChangePct().doubleValue(), Signal.of(row.getSignal()), Boolean.TRUE.equals(row.getHot()), row.getSub());
    }
}
