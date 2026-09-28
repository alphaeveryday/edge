package com.edge.app.etf.service;

import com.edge.app.etf.dto.ChartResponse;
import com.edge.app.etf.dto.EtfDetailResponse;
import com.edge.app.etf.dto.EtfSummaryResponse;
import com.edge.app.etf.dto.MoveResponse;
import org.springframework.stereotype.Service;

import java.util.List;

/** 스텁. */
@Service
public class EtfService {
    public List<EtfSummaryResponse> list(String q) {
        return List.of(EtfExamples.summary());
    }

    public EtfSummaryResponse get(String code) {
        return EtfExamples.summary();
    }

    public ChartResponse chart(String code, String range) {
        return EtfExamples.chart(range);
    }

    public MoveResponse move(String code) {
        return EtfExamples.move();
    }

    public EtfDetailResponse detail(String code) {
        return EtfExamples.detail();
    }
}
