package com.edge.app.application.etf.provided;

import com.edge.app.application.etf.required.EtfRepository;
import com.edge.app.domain.etf.Signal;

public record EtfSummaryResponse(String code, String name, String theme, double price, double changePct,
        Signal signal, boolean hot, String sub) {
    public static EtfSummaryResponse from(EtfRepository.SummaryRow row) {
        return new EtfSummaryResponse(row.getCode(), row.getName(), row.getThemeKey(), row.getPrice().doubleValue(),
                row.getChangePct().doubleValue(), Signal.of(row.getSignal()), Boolean.TRUE.equals(row.getHot()), row.getSub());
    }
}
