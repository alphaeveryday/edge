package com.edge.app.batch.sync;

import lombok.RequiredArgsConstructor;
import org.springframework.batch.infrastructure.item.Chunk;
import org.springframework.batch.infrastructure.item.ItemWriter;
import org.springframework.stereotype.Component;

/** step 트랜잭션 안의 ETF 단위 앱 테이블 반영 */
@Component
@RequiredArgsConstructor
class EtfWriter implements ItemWriter<EtfSnapshot> {
    private final SyncRepository sync;

    @Override
    public void write(Chunk<? extends EtfSnapshot> chunk) {
        for (EtfSnapshot s : chunk) {
            String code = s.curation().code();
            sync.upsertEtf(code, s.etf().instrumentId(), s.etf().marketCode(), s.etf().name(), s.curation());
            if (s.quote() != null) {
                sync.upsertQuote(code, s.quote().price(), s.quote().changePct(), s.quote().asOf());
                sync.replaceCandles(code, s.closes());
            }
            sync.upsertDetail(code, s.detailAsOf(), s.detail());
            sync.replaceMoves(code, s.moves());
            sync.deleteAnalysesExcept(code, s.outlooks().stream().map(o -> o.analysis().asOf()).toList());
            for (EtfSnapshot.Outlook o : s.outlooks()) {
                sync.replaceAxes(sync.upsertAnalysis(code, o.analysis(), o.signal()), o.axes());
            }
        }
    }
}
