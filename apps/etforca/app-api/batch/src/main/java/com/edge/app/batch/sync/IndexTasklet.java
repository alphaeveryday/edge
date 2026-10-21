package com.edge.app.batch.sync;

import com.edge.app.batch.sync.PipelineRepository.Close;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.batch.core.step.StepContribution;
import org.springframework.batch.core.scope.context.ChunkContext;
import org.springframework.batch.core.step.tasklet.Tasklet;
import org.springframework.batch.infrastructure.repeat.RepeatStatus;
import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

import static com.edge.app.batch.sync.SyncFormat.KST;
import static com.edge.app.batch.sync.SyncFormat.MARKET_CLOSE;
import static com.edge.app.batch.sync.SyncFormat.pct;

/** 홈 지수의 직전 종가 대비 동기화, 지수 하나의 실패가 다른 지수를 막지 않음 */
@Slf4j
@Component
@RequiredArgsConstructor
class IndexTasklet implements Tasklet {
    private static final Map<String, String> INDICES = Map.of("1001", "코스피", "2001", "코스닥");

    private final PipelineRepository pipeline;
    private final SyncRepository sync;

    @Override
    public RepeatStatus execute(StepContribution contribution, ChunkContext context) {
        INDICES.forEach(this::syncIndex);
        return RepeatStatus.FINISHED;
    }

    private void syncIndex(String code, String name) {
        try {
            String id = pipeline.index(code).orElse(null);
            List<Close> closes = id == null ? List.of() : pipeline.indexCloses(id, LocalDate.now(KST).minusDays(14));
            if (closes.isEmpty()) {
                log.info("index sync skipped code={} reason=no-data", code);
                return;
            }
            Close last = closes.getLast();
            Close prev = closes.size() < 2 ? last : closes.get(closes.size() - 2);
            sync.upsertIndex(code, name, last.close(), pct(prev.close(), last.close()),
                    last.date().atTime(MARKET_CLOSE).atZone(KST).toInstant());
        } catch (RuntimeException e) {
            log.warn("index sync failed code={}", code, e);
        }
    }
}
