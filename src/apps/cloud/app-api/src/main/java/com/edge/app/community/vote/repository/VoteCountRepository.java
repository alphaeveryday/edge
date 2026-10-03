package com.edge.app.community.vote.repository;

import com.edge.app.community.vote.entity.Vote;
import com.edge.app.community.vote.entity.VoteChoice;

import com.edge.app.common.config.RedisCircuit;
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

    // 열린 서킷이 DB 저장을 막지 않도록 Redis 호출에만 거는 쓰기 서킷
    // 열린 서킷의 재조정 차단을 막는 복구 경로 replace() 의 서킷 제외
    public void vote(String etfCode, Long memberId, VoteChoice choice) {
        try {
            // StringRedisTemplate 의 스크립트 인자 String 직렬화
            // Long 인자 전달 시 ClassCastException 발생
            circuit.of(etfCode).executeRunnable(
                    () -> redisTemplate.execute(VOTE, generateKeys(etfCode), memberId.toString(), choice.value()));
        } catch (Exception ex) {
            meterRegistry.counter("vote.redis.write.failures").increment();
            log.warn("Redis vote failed etf={}; DB committed", etfCode, ex);
        }
    }

    public VoteCounts counts(String etfCode) {
        Map<Object, Object> values = redisTemplate.opsForHash().entries(generateKeys(etfCode).get(1));
        return new VoteCounts(count(values, VoteChoice.BUY), count(values, VoteChoice.WAIT),
                count(values, VoteChoice.SELL));
    }

    public VoteReconcileResult replace(String etfCode, List<Vote> votes) {
        List<String> args = new ArrayList<>();
        for (VoteChoice choice : VoteChoice.values()) {
            args.add(Long.toString(votes.stream().filter(v -> v.getChoice() == choice).count()));
        }
        votes.forEach(v -> {
            args.add(v.getMemberId().toString());
            args.add(v.getChoice().value());
        });
        List<?> result = redisTemplate.execute(REPLACE, generateKeys(etfCode), args.toArray());
        return new VoteReconcileResult(((Number) result.get(0)).longValue(),
                ((Number) result.get(1)).longValue(), ((Number) result.get(2)).longValue());
    }

    private static long count(Map<Object, Object> values, VoteChoice choice) {
        return Long.parseLong(values.getOrDefault(choice.value(), "0").toString());
    }

    private static <T> DefaultRedisScript<T> script(String path, Class<T> type) {
        var script = new DefaultRedisScript<T>();
        script.setLocation(new ClassPathResource(path));
        script.setResultType(type);
        return script;
    }

    private List<String> generateKeys(String etfCode) {
        return List.of(CHOICES_KEY_FORMAT.formatted(etfCode), COUNT_KEY_FORMAT.formatted(etfCode));
    }
}
