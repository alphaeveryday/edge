package com.edge.app.repository;

import com.edge.app.dto.VoteCountResponse;
import com.edge.app.entity.ForecastStatus;
import com.edge.app.entity.VoteChoice;
import com.edge.app.error.RedisUnavailableException;
import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import lombok.RequiredArgsConstructor;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.data.redis.core.script.RedisScript;
import org.springframework.stereotype.Repository;

import java.time.Duration;
import java.util.List;
import java.util.Set;
import java.util.function.Supplier;

// 투표의 주 저장소(Redis) 접근 — write-behind 의 선반영 쪽. DB 백업은 VoteBackUpRepository.
@Repository
@RequiredArgsConstructor
public class VoteRepository {

	private static final Duration STATE_TTL = Duration.ofSeconds(30);
	private static final String DIRTY_FORECASTS = "dirty:forecasts";

	// seq 는 Redis TIME 으로 발번 — 단일 시계라 도착 순서 = seq 순서 (앱 시계 불사용).
	// 연타: 락·마커 없음. 단일 스레드 도착 순서가 곧 최종값이고 agg 는 정확히 토글된다.
	@SuppressWarnings("rawtypes")
	private static final RedisScript<List> VOTE_SCRIPT = new DefaultRedisScript<>("""
			local user, choice, fid = ARGV[1], ARGV[2], ARGV[3]
			local t = redis.call('TIME')
			local seq = string.format('%d%06d', t[1], t[2])
			local old = redis.call('HGET', KEYS[1], user)
			local oldChoice
			if old then
				local i = string.find(old, ':', 1, true)
				oldChoice = string.sub(old, 1, i - 1)
				if tonumber(string.sub(old, i + 1)) >= tonumber(seq) then
					return {old, redis.call('HGET', KEYS[2], 'AGREE') or '0',
							redis.call('HGET', KEYS[2], 'DISAGREE') or '0'}
				end
			end
			redis.call('HSET', KEYS[1], user, choice .. ':' .. seq)
			if oldChoice and oldChoice ~= choice then
				redis.call('HINCRBY', KEYS[2], oldChoice, -1)
			end
			if (not oldChoice) or oldChoice ~= choice then
				redis.call('HINCRBY', KEYS[2], choice, 1)
			end
			redis.call('SADD', KEYS[3], user)
			redis.call('SADD', KEYS[4], fid)
			return {choice .. ':' .. seq, redis.call('HGET', KEYS[2], 'AGREE') or '0',
					redis.call('HGET', KEYS[2], 'DISAGREE') or '0'}
			""", List.class);

	// flush 대상 확보 — SPOP→SADD 한 스크립트 (DB 커밋 전 사망 시 flushing 에 남아 복귀 가능)
	@SuppressWarnings("rawtypes")
	private static final RedisScript<List> CLAIM_SCRIPT = new DefaultRedisScript<>("""
			local users = redis.call('SPOP', KEYS[1], tonumber(ARGV[1]))
			if #users > 0 then
				redis.call('SADD', KEYS[2], unpack(users))
			end
			return users
			""", List.class);

	// 복구 재조정: DB 행을 seq 비교 후 반영 (더 새로운 Redis 값은 보존)
	private static final RedisScript<Long> APPLY_SCRIPT = new DefaultRedisScript<>("""
			local user, choice, seq = ARGV[1], ARGV[2], tonumber(ARGV[3])
			local old = redis.call('HGET', KEYS[1], user)
			local oldChoice
			if old then
				local i = string.find(old, ':', 1, true)
				oldChoice = string.sub(old, 1, i - 1)
				if tonumber(string.sub(old, i + 1)) >= seq then
					return 0
				end
			end
			redis.call('HSET', KEYS[1], ARGV[1], choice .. ':' .. ARGV[3])
			if oldChoice and oldChoice ~= choice then
				redis.call('HINCRBY', KEYS[2], oldChoice, -1)
			end
			if (not oldChoice) or oldChoice ~= choice then
				redis.call('HINCRBY', KEYS[2], choice, 1)
			end
			return 1
			""", Long.class);

	private final StringRedisTemplate redis;
	private final CircuitBreaker circuitBreaker;

	/** @return [최종 choice:seq, agree, disagree] 의 집계 부분 */
	public VoteCountResponse vote(long forecastId, long userId, VoteChoice choice) {
		List<?> result = guarded(() -> redis.execute(VOTE_SCRIPT,
				List.of(voteKey(forecastId), aggKey(forecastId), dirtyKey(forecastId), DIRTY_FORECASTS),
				String.valueOf(userId), choice.name(), String.valueOf(forecastId)));
		return new VoteCountResponse(
				Long.parseLong(String.valueOf(result.get(1))),
				Long.parseLong(String.valueOf(result.get(2))));
	}

	public VoteCountResponse counts(long forecastId) {
		List<String> counts = guarded(() ->
				redis.opsForHash().multiGet(aggKey(forecastId), List.of("AGREE", "DISAGREE")).stream()
						.map(v -> v == null ? "0" : String.valueOf(v))
						.toList());
		return new VoteCountResponse(Long.parseLong(counts.get(0)), Long.parseLong(counts.get(1)));
	}

	public ForecastStatus cachedState(long forecastId) {
		String state = guarded(() -> redis.opsForValue().get(stateKey(forecastId)));
		return state == null ? null : ForecastStatus.valueOf(state);
	}

	public void cacheState(long forecastId, ForecastStatus status) {
		guarded(() -> {
			redis.opsForValue().set(stateKey(forecastId), status.name(), STATE_TTL);
			return null;
		});
	}

	public boolean tryFlushLeader(Duration ttl) {
		return Boolean.TRUE.equals(guarded(() ->
				redis.opsForValue().setIfAbsent("flush:leader", "1", ttl)));
	}

	public Set<String> dirtyForecasts() {
		return guarded(() -> redis.opsForSet().members(DIRTY_FORECASTS));
	}

	public void clearDirtyForecastIfDrained(long forecastId) {
		guarded(() -> {
			Long size = redis.opsForSet().size(dirtyKey(forecastId));
			if (size == null || size == 0) {
				redis.opsForSet().remove(DIRTY_FORECASTS, String.valueOf(forecastId));
			}
			return null;
		});
	}

	public List<String> claimDirty(long forecastId, String jobId, int batchSize) {
		return guarded(() -> {
			List<?> users = redis.execute(CLAIM_SCRIPT,
					List.of(dirtyKey(forecastId), flushingKey(forecastId, jobId)),
					String.valueOf(batchSize));
			return users.stream().map(String::valueOf).toList();
		});
	}

	/** @return user 순서대로 "choice:seq" (없으면 null) */
	public List<Object> readVotes(long forecastId, List<String> users) {
		return guarded(() -> redis.opsForHash().multiGet(voteKey(forecastId), List.copyOf(users)));
	}

	public void ackFlushing(long forecastId, String jobId) {
		guarded(() -> redis.delete(flushingKey(forecastId, jobId)));
	}

	public void requeueFlushing(long forecastId, String jobId) {
		guarded(() -> {
			Set<String> users = redis.opsForSet().members(flushingKey(forecastId, jobId));
			if (users != null && !users.isEmpty()) {
				redis.opsForSet().add(dirtyKey(forecastId), users.toArray(String[]::new));
			}
			redis.delete(flushingKey(forecastId, jobId));
			return null;
		});
	}

	/** @return 반영됐으면 true (Redis 값이 더 새로우면 false) */
	public boolean applyReconciled(long forecastId, long userId, VoteChoice choice, long seq) {
		Long applied = guarded(() -> redis.execute(APPLY_SCRIPT,
				List.of(voteKey(forecastId), aggKey(forecastId)),
				String.valueOf(userId), choice.name(), String.valueOf(seq)));
		return applied != null && applied == 1;
	}

	private String voteKey(long f) {
		return "vote:{" + f + "}";
	}

	private String aggKey(long f) {
		return "agg:{" + f + "}";
	}

	private String dirtyKey(long f) {
		return "dirty:{" + f + "}";
	}

	private String flushingKey(long f, String jobId) {
		return "flushing:{" + f + "}:" + jobId;
	}

	private String stateKey(long f) {
		return "state:{" + f + "}";
	}

	private <T> T guarded(Supplier<T> action) {
		try {
			return circuitBreaker.executeSupplier(action);
		} catch (CallNotPermittedException | DataAccessException e) {
			throw new RedisUnavailableException(e);
		}
	}
}
