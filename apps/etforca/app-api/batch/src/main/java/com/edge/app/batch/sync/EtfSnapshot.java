package com.edge.app.batch.sync;

import com.edge.app.batch.sync.PipelineRepository.Close;
import com.edge.app.batch.sync.PipelineRepository.Instrument;
import com.edge.app.batch.sync.SyncRepository.Axis;
import com.edge.app.batch.sync.SyncRepository.Curation;
import com.edge.app.batch.sync.SyncRepository.Dated;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;

/** ETF 하나의 앱 테이블 반영분, 시세 없는 ETF 의 quote null */
record EtfSnapshot(Curation curation, Instrument etf, Quote quote, List<Close> closes, LocalDate detailAsOf, String detail,
        List<Dated> moves, List<Outlook> outlooks) {

    record Quote(BigDecimal price, BigDecimal changePct, Instant asOf) {
    }

    record Outlook(Dated analysis, String signal, List<Axis> axes) {
    }
}
