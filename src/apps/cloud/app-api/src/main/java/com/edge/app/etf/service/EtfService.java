package com.edge.app.etf.service;

import com.edge.app.common.AppErrorStatus;
import com.edge.app.common.json.Payload;
import com.edge.app.etf.dto.ChartResponse;
import com.edge.app.etf.dto.EtfDetailResponse;
import com.edge.app.etf.dto.EtfSummaryResponse;
import com.edge.app.etf.dto.MoveResponse;
import com.edge.app.etf.entity.ChartRange;
import com.edge.app.etf.entity.Dir;
import com.edge.app.etf.entity.EtfCandle;
import com.edge.app.etf.entity.EtfMove;
import com.edge.app.etf.repository.EtfCandleRepository;
import com.edge.app.etf.repository.EtfDetailRepository;
import com.edge.app.etf.repository.EtfMoveRepository;
import com.edge.app.etf.repository.EtfRepository;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
@RequiredArgsConstructor
public class EtfService {
    private static final DateTimeFormatter AXIS = DateTimeFormatter.ofPattern("M/d");

    private final EtfRepository etfRepository;
    private final EtfCandleRepository candleRepository;
    private final EtfMoveRepository moveRepository;
    private final EtfDetailRepository detailRepository;

    @Transactional(readOnly = true)
    public List<EtfSummaryResponse> list(String q) {
        return etfRepository.search(q == null ? "" : q.trim()).stream().map(EtfSummaryResponse::from).toList();
    }

    @Transactional(readOnly = true)
    public EtfSummaryResponse get(String code) {
        return etfRepository.summaries(List.of(code)).stream().findFirst().map(EtfSummaryResponse::from)
                .orElseThrow(() -> new GeneralException(AppErrorStatus.ETF_NOT_FOUND));
    }

    /** 구간은 최신 일봉 기준. ma5·ma20 은 앞 행을 더 읽어 계산하고 모자라면 null. */
    @Transactional(readOnly = true)
    public ChartResponse chart(String code, ChartRange range) {
        requireEtf(code);
        EtfCandle latest = candleRepository.findTopByEtfCodeOrderByTradeDateDesc(code).orElse(null);
        if (latest == null) {
            return new ChartResponse(range.value(), List.of(), List.of(), List.of(), List.of());
        }
        var from = range.from(latest.getTradeDate());
        List<EtfCandle> visible = candleRepository.findByEtfCodeAndTradeDateGreaterThanEqualOrderByTradeDate(code, from);
        List<EtfCandle> lead = new ArrayList<>(candleRepository.findTop19ByEtfCodeAndTradeDateLessThanOrderByTradeDateDesc(code, from));
        java.util.Collections.reverse(lead);
        List<Double> closes = new ArrayList<>();
        lead.forEach(c -> closes.add(c.getClose().doubleValue()));
        visible.forEach(c -> closes.add(c.getClose().doubleValue()));
        int offset = lead.size();
        return new ChartResponse(range.value(),
                visible.stream().map(c -> new ChartResponse.Candle(c.getOpen().doubleValue(), c.getHigh().doubleValue(),
                        c.getLow().doubleValue(), c.getClose().doubleValue())).toList(),
                movingAverage(closes, offset, 5), movingAverage(closes, offset, 20),
                visible.stream().map(c -> AXIS.format(c.getTradeDate())).toList());
    }

    /** 발행본이 없거나 summary 가 null 이면 ANALYSIS4001(2026-09-28 결정). */
    @Transactional(readOnly = true)
    public MoveResponse move(String code) {
        requireEtf(code);
        EtfMove move = moveRepository.findTopByEtfCodeOrderByAsOfDesc(code)
                .orElseThrow(() -> new GeneralException(AppErrorStatus.ANALYSIS_NOT_READY));
        Payload payload = Payload.parse(move.getPayload());
        if (payload.get("summary").isNull()) {
            throw new GeneralException(AppErrorStatus.ANALYSIS_NOT_READY);
        }
        List<Payload> items = payload.list("items");
        Map<String, List<MoveResponse.Item>> groups = new LinkedHashMap<>();
        for (Payload item : items) {
            groups.computeIfAbsent(item.text("type"), k -> new ArrayList<>())
                    .add(new MoveResponse.Item(Dir.fold(item.text("sentiment")), item.text("title_keyword"), item.text("sentence")));
        }
        return new MoveResponse(move.getPublishedAt(), payload.text("summary"), "관련 이슈 " + items.size() + "개",
                payload.text("summary"),
                groups.entrySet().stream().map(e -> new MoveResponse.Group(e.getKey(), e.getValue())).toList());
    }

    /** payload 는 EtfDetailData 전체(erd.md). 없으면 ANALYSIS4001. */
    @Transactional(readOnly = true)
    public EtfDetailResponse detail(String code) {
        requireEtf(code);
        return detailRepository.findById(code).map(d -> Payload.parse(d.getPayload()).as(EtfDetailResponse.class))
                .orElseThrow(() -> new GeneralException(AppErrorStatus.ANALYSIS_NOT_READY));
    }

    private void requireEtf(String code) {
        if (!etfRepository.existsById(code)) {
            throw new GeneralException(AppErrorStatus.ETF_NOT_FOUND);
        }
    }

    private static List<Double> movingAverage(List<Double> closes, int offset, int window) {
        List<Double> out = new ArrayList<>();
        for (int i = offset; i < closes.size(); i++) {
            if (i + 1 < window) {
                out.add(null);
                continue;
            }
            double sum = 0;
            for (int j = i + 1 - window; j <= i; j++) {
                sum += closes.get(j);
            }
            out.add(Math.round(sum / window * 100) / 100.0);
        }
        return out;
    }
}
