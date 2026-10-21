package com.edge.app.batch.sync;

import com.edge.app.batch.sync.SyncRepository.Latest;
import com.edge.app.batch.sync.SyncRepository.Rank;
import lombok.RequiredArgsConstructor;
import org.springframework.batch.core.step.StepContribution;
import org.springframework.batch.core.scope.context.ChunkContext;
import org.springframework.batch.core.step.tasklet.Tasklet;
import org.springframework.batch.infrastructure.repeat.RepeatStatus;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.stream.StreamSupport;

import static com.edge.app.batch.sync.SyncFormat.JSON;
import static com.edge.app.batch.sync.SyncFormat.KST;

/**
 * 가장 최근 발행일 전망의 5단계 강한 순 탐색 순위
 * 같은 단계의 최근 발행 순 정렬
 */
@Component
@RequiredArgsConstructor
class RankTasklet implements Tasklet {
    private static final List<String> SIGNALS = List.of("strongUp", "up", "neutral", "down", "strongDown");

    private final SyncRepository sync;

    @Override
    public RepeatStatus execute(StepContribution contribution, ChunkContext context) {
        List<Latest> latest = sync.latestAnalyses();
        LocalDate asOf = latest.stream().map(Latest::asOf).max(Comparator.naturalOrder()).orElse(LocalDate.now(KST));
        List<Latest> today = latest.stream().filter(l -> l.asOf().equals(asOf))
                .sorted(Comparator.comparingInt((Latest l) -> SIGNALS.indexOf(l.signal()))
                        .thenComparing(Latest::publishedAt, Comparator.reverseOrder()))
                .toList();
        List<Rank> ranks = new ArrayList<>();
        for (Latest l : today) {
            JsonNode p = JSON.readTree(l.payload());
            List<JsonNode> chips = list(p.path("conclusion").path("supports"));
            if (chips.isEmpty()) {
                chips = list(p.path("conclusion").path("burdens"));
            }
            ranks.add(new Rank(ranks.size() + 1, l.code(), text(p.path("summary_card").path("title")),
                    JSON.writeValueAsString(chips.stream().limit(2).map(c -> text(c.path("label"))).toList())));
        }
        sync.replaceRank(asOf, ranks);
        return RepeatStatus.FINISHED;
    }

    private static List<JsonNode> list(JsonNode node) {
        return StreamSupport.stream(node.spliterator(), false).toList();
    }

    // 없거나 값이 아닌 노드의 빈 문자열
    private static String text(JsonNode node) {
        return node.isValueNode() && !node.isNull() ? node.asText() : "";
    }
}
