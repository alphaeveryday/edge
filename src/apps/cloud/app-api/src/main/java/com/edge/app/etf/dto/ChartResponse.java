package com.edge.app.etf.dto;

import java.util.List;

public record ChartResponse(String range, List<Candle> candles, List<Double> ma5, List<Double> ma20, List<String> axis) {
    public record Candle(double o, double h, double l, double c) {
    }
}
