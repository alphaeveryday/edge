package com.edge.app.community.vote.repository.writebehind;

import com.edge.app.community.vote.entity.Vote;
import com.edge.app.community.vote.entity.VoteChoice;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.io.ClassPathResource;
import org.springframework.data.redis.core.Cursor;
import org.springframework.data.redis.core.ScanOptions;
import jakarta.annotation.PostConstruct;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

@Component
@ConditionalOnProperty(name = "vote.mode", havingValue = "write-behind")
@RequiredArgsConstructor
public class VoteBufferRepository {
    private final StringRedisTemplate redisTemplate;
    private final RedisConnectionFactory connectionFactory;
    private static final String DIRTY_FORECASTS = "vote:dirty-etfs";
    private static final DefaultRedisScript<Long> RECORD = script("writebehind/vote-buffer.lua");
    private static final DefaultRedisScript<Long> CLEAR = script("writebehind/clear-dirty.lua");
    private static final DefaultRedisScript<Long> RELEASE = script("writebehind/release-dirty.lua");
    private static final DefaultRedisScript<Long> WARM = script("writebehind/warm.lua");

    // 전망별 키({etfCode})와 전역 dirty 집합이 다른 슬롯이라 Cluster 에선 다중 키 Lua 가 CROSSSLOT 으로 전건 실패한다.
    @PostConstruct
    void rejectCluster() {
        if (connectionFactory instanceof LettuceConnectionFactory lettuce && lettuce.isClusterAware()) {
            throw new IllegalStateException("vote.mode=write-behind does not support Redis Cluster (CROSSSLOT on vote:dirty-etfs)");
        }
    }

    public boolean record(String etfCode, Long memberId, VoteChoice choice) {
        List<String> keys = List.of(key(etfCode, "choices"), key(etfCode, "count"), key(etfCode, "dirty"), DIRTY_FORECASTS);
        return redisTemplate.execute(RECORD, keys, memberId.toString(), choice.value(), etfCode) == 1;
    }

    public Set<String> dirtyEtfs() {
        Set<String> members = redisTemplate.opsForSet().members(DIRTY_FORECASTS);
        return members == null ? Set.of() : Set.copyOf(members);
    }

    public Map<Long, VoteChoice> readDirty(String etfCode, int batchSize) {
        Map<Long, VoteChoice> batch = new HashMap<>();
        try (Cursor<Map.Entry<String, String>> cursor = redisTemplate.<String, String>opsForHash()
                .scan(key(etfCode, "dirty"), ScanOptions.scanOptions().count(batchSize).build())) {
            while (cursor.hasNext() && batch.size() < batchSize) {
                var entry = cursor.next();
                batch.put(Long.valueOf(entry.getKey()), VoteChoice.of(entry.getValue()));
            }
        }
        return batch;
    }

    public boolean clearDirtyIfUnchanged(String etfCode, Long memberId, VoteChoice choice) {
        return redisTemplate.execute(CLEAR, List.of(key(etfCode, "dirty")), memberId.toString(), choice.value()) == 1;
    }

    public boolean releaseIfClean(String etfCode) {
        return redisTemplate.execute(RELEASE, List.of(key(etfCode, "dirty"), DIRTY_FORECASTS), etfCode) == 1;
    }

    public long mergeMissing(String etfCode, List<Vote> votes) {
        List<String> args = new ArrayList<>();
        votes.forEach(v -> {
            args.add(v.getMemberId().toString());
            args.add(v.getChoice().value());
        });
        return redisTemplate.execute(WARM, List.of(key(etfCode, "choices"), key(etfCode, "count")), args.toArray());
    }

    public long dirtySize(String etfCode) {
        return redisTemplate.opsForHash().size(key(etfCode, "dirty"));
    }

    private static String key(String etfCode, String suffix) {
        return "vote:{%s}:%s".formatted(etfCode, suffix);
    }

    private static DefaultRedisScript<Long> script(String path) {
        var script = new DefaultRedisScript<Long>();
        script.setLocation(new ClassPathResource(path));
        script.setResultType(Long.class);
        return script;
    }
}
