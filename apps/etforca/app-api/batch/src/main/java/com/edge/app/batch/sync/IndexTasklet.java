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
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static com.edge.app.batch.sync.SyncFormat.KST;
import static com.edge.app.batch.sync.SyncFormat.MARKET_CLOSE;
import static com.edge.app.batch.sync.SyncFormat.pct;

/** 홈 지수의 직전 종가 대비 동기화, 지수 하나의 실패가 다른 지수를 막지 않고 끝에 step 실패 */
@Slf4j
@Component
@RequiredArgsConstructor
class IndexTasklet implements Tasklet {
    // 코스피·코스닥 순서 고정
    private static final Map<String, String> INDICES = new LinkedHashMap<>();

    static {
        INDICES.put("1001", "코스피");
        INDICES.put("2001", "코스닥");
    }

    private final PipelineRepository pipeline;
    private final SyncRepository sync;

    @Override
    public RepeatStatus execute(StepContribution contribution, ChunkContext context) {
        List<String> failed = new ArrayList<>();
        INDICES.forEach((code, name) -> {
            if (!syncIndex(code, name)) {
                failed.add(code);
            }
        });
        // 모든 지수 시도 뒤 실패의 step 실패 전파
        if (!failed.isEmpty()) {
            throw new IllegalStateException("index sync failed codes=" + failed);
        }
        return RepeatStatus.FINISHED;
    }

    // 원천에 데이터 없는 지수의 실패 아닌 건너뜀
    private boolean syncIndex(String code, String name) {
        try {
            String id = pipeline.index(code).orElse(null);
            List<Close> closes = id == null ? List.of() : pipeline.indexCloses(id, LocalDate.now(KST).minusDays(14));
            if (closes.isEmpty()) {
                log.info("index sync skipped code={} reason=no-data", code);
                return true;
            }
            Close last = closes.getLast();
            Close prev = closes.size() < 2 ? last : closes.get(closes.size() - 2);
            sync.upsertIndex(code, name, last.close(), pct(prev.close(), last.close()),
                    last.date().atTime(MARKET_CLOSE).atZone(KST).toInstant());
            return true;
        } catch (RuntimeException e) {
            log.warn("index sync failed code={}", code, e);
            return false;
        }
    }
}
