package com.edge.app.repository.writebehind;

import com.edge.app.entity.Vote;
import com.edge.app.entity.VoteChoice;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.io.ClassPathResource;
import org.springframework.data.redis.core.Cursor;
import org.springframework.data.redis.core.ScanOptions;
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
    private static final String DIRTY_FORECASTS = "vote:dirty-forecasts";
    private static final DefaultRedisScript<Long> RECORD = script("writebehind/vote-buffer.lua");
    private static final DefaultRedisScript<Long> CLEAR = script("writebehind/clear-dirty.lua");
    private static final DefaultRedisScript<Long> RELEASE = script("writebehind/release-dirty.lua");
    private static final DefaultRedisScript<Long> WARM = script("writebehind/warm.lua");

    public boolean record(Long forecastId, Long userId, VoteChoice choice) {
        List<String> keys = List.of(key(forecastId, "choices"), key(forecastId, "count"), key(forecastId, "dirty"), DIRTY_FORECASTS);
        return redisTemplate.execute(RECORD, keys, userId.toString(), choice.name(), forecastId.toString()) == 1;
    }

    public Set<Long> dirtyForecasts() {
        Set<String> members = redisTemplate.opsForSet().members(DIRTY_FORECASTS);
        return members == null ? Set.of() : members.stream().map(Long::valueOf).collect(Collectors.toSet());
    }

    public Map<Long, VoteChoice> readDirty(Long forecastId, int batchSize) {
        Map<Long, VoteChoice> batch = new HashMap<>();
        try (Cursor<Map.Entry<String, String>> cursor = redisTemplate.<String, String>opsForHash()
                .scan(key(forecastId, "dirty"), ScanOptions.scanOptions().count(batchSize).build())) {
            while (cursor.hasNext() && batch.size() < batchSize) {
                var entry = cursor.next();
                batch.put(Long.valueOf(entry.getKey()), VoteChoice.valueOf(entry.getValue()));
            }
        }
        return batch;
    }

    public boolean clearDirtyIfUnchanged(Long forecastId, Long userId, VoteChoice choice) {
        return redisTemplate.execute(CLEAR, List.of(key(forecastId, "dirty")), userId.toString(), choice.name()) == 1;
    }

    public boolean releaseIfClean(Long forecastId) {
        return redisTemplate.execute(RELEASE, List.of(key(forecastId, "dirty"), DIRTY_FORECASTS), forecastId.toString()) == 1;
    }

    public long mergeMissing(Long forecastId, List<Vote> votes) {
        List<String> args = new ArrayList<>();
        votes.forEach(v -> {
            args.add(v.getUserId().toString());
            args.add(v.getChoice().name());
        });
        return redisTemplate.execute(WARM, List.of(key(forecastId, "choices"), key(forecastId, "count")), args.toArray());
    }

    public long dirtySize(Long forecastId) {
        return redisTemplate.opsForHash().size(key(forecastId, "dirty"));
    }

    private static String key(Long forecastId, String suffix) {
        return "vote:{%s}:%s".formatted(forecastId, suffix);
    }

    private static DefaultRedisScript<Long> script(String path) {
        var script = new DefaultRedisScript<Long>();
        script.setLocation(new ClassPathResource(path));
        script.setResultType(Long.class);
        return script;
    }
}
