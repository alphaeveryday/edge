package com.edge.app.home.dto;

import com.edge.app.home.entity.MarketIndex;

import java.math.BigDecimal;
import java.time.Instant;

public record MarketIndexResponse(String code, String name, BigDecimal value, BigDecimal changePct, Instant asOf) {
    public static MarketIndexResponse from(MarketIndex i) {
        return new MarketIndexResponse(i.getCode(), i.getName(), i.getPrice(), i.getChangePct(), i.getAsOf());
    }
}
