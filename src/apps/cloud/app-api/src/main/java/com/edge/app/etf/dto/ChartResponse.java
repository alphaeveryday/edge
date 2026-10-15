package com.edge.app.etf.dto;

import java.util.List;

public record ChartResponse(String range, List<Candle> candles, List<Double> ma5, List<Double> ma20, List<String> axis) {
    // 시가·고가·저가는 원천에 없으면 생략
    public record Candle(Double o, Double h, Double l, double c) {
    }
}
