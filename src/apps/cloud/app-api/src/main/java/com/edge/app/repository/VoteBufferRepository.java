package com.edge.app.repository;

import com.edge.app.entity.Vote;
import com.edge.app.entity.VoteChoice;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.io.ClassPathResource;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
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
    private static final DefaultRedisScript<Long> RECORD = script("vote-buffer.lua");
    private static final DefaultRedisScript<Long> CLEAR = script("clear-dirty.lua");
    private static final DefaultRedisScript<Long> RELEASE = script("release-dirty.lua");
    private static final DefaultRedisScript<Long> WARM = script("warm.lua");

    public boolean record(Long forecastId, Long userId, VoteChoice choice) {
        List<String> keys = List.of(key(forecastId, "choices"), key(forecastId, "count"), key(forecastId, "dirty"), DIRTY_FORECASTS);
        return redisTemplate.execute(RECORD, keys, userId.toString(), choice.name(), forecastId.toString()) == 1;
    }

    public Set<Long> dirtyForecasts() {
        Set<String> members = redisTemplate.opsForSet().members(DIRTY_FORECASTS);
        return members == null ? Set.of() : members.stream().map(Long::valueOf).collect(Collectors.toSet());
    }

    public Map<Long, VoteChoice> readDirty(Long forecastId, int batchSize) {
        return redisTemplate.<String, String>opsForHash().entries(key(forecastId, "dirty")).entrySet().stream()
                .limit(batchSize)
                .collect(Collectors.toMap(e -> Long.valueOf(e.getKey()), e -> VoteChoice.valueOf(e.getValue())));
    }

    public boolean clearDirtyIfUnchanged(Long forecastId, Long userId, VoteChoice choice) {
        return redisTemplate.execute(CLEAR, List.of(key(forecastId, "dirty")), userId.toString(), choice.name()) == 1;
    }

    public boolean releaseIfClean(Long forecastId) {
        return redisTemplate.execute(RELEASE, List.of(key(forecastId, "dirty"), DIRTY_FORECASTS), forecastId.toString()) == 1;
    }

    public boolean loadIfAbsent(Long forecastId, List<Vote> votes) {
        List<String> args = new ArrayList<>();
        for (VoteChoice choice : VoteChoice.values()) {
            args.add(Long.toString(votes.stream().filter(v -> v.getChoice() == choice).count()));
        }
        votes.forEach(v -> {
            args.add(v.getUserId().toString());
            args.add(v.getChoice().name());
        });
        List<String> keys = List.of(key(forecastId, "choices"), key(forecastId, "count"), key(forecastId, "dirty"));
        return redisTemplate.execute(WARM, keys, args.toArray()) == 1;
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
