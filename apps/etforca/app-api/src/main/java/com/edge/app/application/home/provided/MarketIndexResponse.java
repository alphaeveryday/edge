package com.edge.app.application.home.provided;

import com.edge.app.domain.home.MarketIndex;

import java.math.BigDecimal;
import java.time.Instant;

public record MarketIndexResponse(String code, String name, BigDecimal value, BigDecimal changePct, Instant asOf) {
    public static MarketIndexResponse from(MarketIndex i) {
        return new MarketIndexResponse(i.getCode(), i.getName(), i.getPrice(), i.getChangePct(), i.getAsOf());
    }
}
