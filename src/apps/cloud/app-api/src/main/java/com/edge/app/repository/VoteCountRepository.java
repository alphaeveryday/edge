package com.edge.app.repository;

import com.edge.app.dto.VoteCounts;
import com.edge.app.dto.VoteReconcileResult;
import com.edge.app.entity.Vote;
import com.edge.app.entity.VoteChoice;
import com.edge.app.config.RedisCircuit;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.core.io.ClassPathResource;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Repository;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Slf4j
@Repository
@RequiredArgsConstructor
public class VoteCountRepository {
    private final StringRedisTemplate redisTemplate;
    private final MeterRegistry meterRegistry;
    private final RedisCircuit circuit;
    private static final String CHOICES_KEY_FORMAT = "vote:{%s}:choices";
    private static final String COUNT_KEY_FORMAT = "vote:{%s}:count";

    private static final DefaultRedisScript<Long> VOTE = script("vote.lua", Long.class);
    private static final DefaultRedisScript<List> REPLACE = script("reconcile.lua", List.class);

    // 서킷이 열려도 DB 저장은 막지 않도록 쓰기 서킷은 여기(Redis 호출)에만 건다.
    // replace()는 복구 경로라 서킷 밖 — 열린 서킷이 재조정까지 차단하면 안 된다.
    public void vote(Long forecastId, Long userId, VoteChoice choice) {
        try {
            // StringRedisTemplate 은 스크립트 인자를 String 으로 직렬화한다 — Long 을 그대로 넘기면 ClassCastException.
            circuit.of(forecastId).executeRunnable(
                    () -> redisTemplate.execute(VOTE, generateKeys(forecastId), userId.toString(), choice.name()));
        } catch (Exception ex) {
            meterRegistry.counter("vote.redis.write.failures").increment();
            log.warn("Redis vote failed forecast={}; DB committed", forecastId, ex);
        }
    }

    public VoteCounts counts(Long forecastId) {
        Map<Object, Object> values = redisTemplate.opsForHash().entries(generateKeys(forecastId).get(1));
        return new VoteCounts(count(values, VoteChoice.BUY), count(values, VoteChoice.HOLD),
                count(values, VoteChoice.SELL));
    }

    public VoteReconcileResult replace(Long forecastId, List<Vote> votes) {
        List<String> args = new ArrayList<>();
        for (VoteChoice choice : VoteChoice.values()) {
            args.add(Long.toString(votes.stream().filter(v -> v.getChoice() == choice).count()));
        }
        votes.forEach(v -> {
            args.add(v.getUserId().toString());
            args.add(v.getChoice().name());
        });
        List<?> result = redisTemplate.execute(REPLACE, generateKeys(forecastId), args.toArray());
        return new VoteReconcileResult(((Number) result.get(0)).longValue(),
                ((Number) result.get(1)).longValue(), ((Number) result.get(2)).longValue());
    }

    private static long count(Map<Object, Object> values, VoteChoice choice) {
        return Long.parseLong(values.getOrDefault(choice.name(), "0").toString());
    }

    private static <T> DefaultRedisScript<T> script(String path, Class<T> type) {
        var script = new DefaultRedisScript<T>();
        script.setLocation(new ClassPathResource(path));
        script.setResultType(type);
        return script;
    }

    private List<String> generateKeys(Long forecastId) {
        return List.of(CHOICES_KEY_FORMAT.formatted(forecastId), COUNT_KEY_FORMAT.formatted(forecastId));
    }
}
