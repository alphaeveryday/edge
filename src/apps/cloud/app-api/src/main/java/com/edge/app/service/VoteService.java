package com.edge.app.service;

import com.edge.app.dto.MyVoteResponse;
import com.edge.app.dto.VoteCountResponse;
import com.edge.app.entity.Forecast;
import com.edge.app.entity.ForecastStatus;
import com.edge.app.entity.VoteChoice;
import com.edge.app.error.AppErrorStatus;
import com.edge.app.error.RedisUnavailableException;
import com.edge.common.exception.GeneralException;
import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.data.redis.core.script.RedisScript;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;

// 투표의 정상 경로는 Redis 선반영(write-behind) — DB 는 BackUpProcessor 를 통해서만 접근한다.
@Slf4j
@Service
public class VoteService {

	private static final Duration STATE_TTL = Duration.ofSeconds(30);
	private static final Duration RECONCILE_MARGIN = Duration.ofSeconds(5);
	private static final int FLUSH_BATCH = 500;
	private static final String DIRTY_FORECASTS = "dirty:forecasts";

	// seq 는 Redis TIME 으로 발번 — 단일 시계라 도착 순서 = seq 순서 (앱 시계 불사용).
	// 연타 처리: 락·마커 없음. 단일 스레드 도착 순서가 곧 최종값이고 agg 는 정확히 토글된다.
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
			redis.call('HSET', KEYS[1], user, choice .. ':' .. seq)
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
	private final BackUpProcessor backUpProcessor;

	private volatile Instant bypassStartedAt;

	public VoteService(StringRedisTemplate redis, CircuitBreaker redisCircuitBreaker,
			BackUpProcessor backUpProcessor) {
		this.redis = redis;
		this.circuitBreaker = redisCircuitBreaker;
		this.backUpProcessor = backUpProcessor;
		redisCircuitBreaker.getEventPublisher().onStateTransition(event -> {
			switch (event.getStateTransition().getToState()) {
				case OPEN, FORCED_OPEN -> {
					if (bypassStartedAt == null) {
						bypassStartedAt = Instant.now();
					}
				}
				case CLOSED -> reconcile();
				default -> {
				}
			}
		});
	}

	// 정상: Redis Lua 선반영 후 200 (DB 반영은 flush 가 배치로).
	// 우회: 브레이커 열림 시 BackUpProcessor 로 DB 직접 upsert.
	public VoteCountResponse vote(long forecastId, long userId, VoteChoice choice) {
		backUpProcessor.requireMember(userId);
		try {
			ForecastStatus state = cachedState(forecastId);
			if (state == null) {
				state = backUpProcessor.status(forecastId);
				cacheState(forecastId, state);
			}
			if (state != ForecastStatus.OPEN) {
				throw new GeneralException(AppErrorStatus.FORECAST_NOT_OPEN);
			}
			List<?> result = guarded(() -> redis.execute(VOTE_SCRIPT,
					List.of(voteKey(forecastId), aggKey(forecastId), dirtyKey(forecastId), DIRTY_FORECASTS),
					String.valueOf(userId), choice.name(), String.valueOf(forecastId)));
			return new VoteCountResponse(
					Long.parseLong(String.valueOf(result.get(1))),
					Long.parseLong(String.valueOf(result.get(2))));
		} catch (RedisUnavailableException e) {
			backUpProcessor.voteBypass(forecastId, userId, choice);
			return backUpProcessor.dbCounts(forecastId);
		}
	}

	public VoteCountResponse counts(Forecast forecast) {
		if (forecast.getStatus() == ForecastStatus.OPEN) {
			try {
				List<String> counts = guarded(() ->
						redis.opsForHash().multiGet(aggKey(forecast.getId()), List.of("AGREE", "DISAGREE"))
								.stream().map(v -> v == null ? "0" : String.valueOf(v)).toList());
				return new VoteCountResponse(Long.parseLong(counts.get(0)), Long.parseLong(counts.get(1)));
			} catch (RedisUnavailableException ignored) {
			}
		}
		return backUpProcessor.dbCounts(forecast.getId());
	}

	public List<MyVoteResponse> myVotes(long userId) {
		return backUpProcessor.myVotes(userId);
	}

	public void markWithdrawn(long forecastId) {
		try {
			cacheState(forecastId, ForecastStatus.WITHDRAWN);
		} catch (RedisUnavailableException ignored) {
			// state 캐시 TTL(30s)이 지나면 DB 재조회로 수렴
		}
	}

	// at-least-once flush: 리더 락 유실로 중복 실행돼도 seq 보호 upsert 가 no-op 으로 흡수.
	@Scheduled(fixedDelay = 3_000)
	public void flush() {
		try {
			boolean leader = Boolean.TRUE.equals(guarded(() ->
					redis.opsForValue().setIfAbsent("flush:leader", "1", Duration.ofSeconds(3))));
			if (!leader) {
				return;
			}
			Set<String> forecastIds = guarded(() -> redis.opsForSet().members(DIRTY_FORECASTS));
			for (String fid : forecastIds) {
				flushForecast(Long.parseLong(fid));
			}
		} catch (RedisUnavailableException ignored) {
			// Redis 불가면 flush 할 것도 없다 — 다음 주기 재시도
		}
	}

	private void flushForecast(long forecastId) {
		String jobId = UUID.randomUUID().toString();
		List<String> users = guarded(() -> {
			List<?> claimed = redis.execute(CLAIM_SCRIPT,
					List.of(dirtyKey(forecastId), flushingKey(forecastId, jobId)),
					String.valueOf(FLUSH_BATCH));
			return claimed.stream().map(String::valueOf).toList();
		});
		if (users.isEmpty()) {
			guarded(() -> {
				Long size = redis.opsForSet().size(dirtyKey(forecastId));
				if (size == null || size == 0) {
					redis.opsForSet().remove(DIRTY_FORECASTS, String.valueOf(forecastId));
				}
				return null;
			});
			return;
		}
		try {
			List<Object> values = guarded(() ->
					redis.opsForHash().multiGet(voteKey(forecastId), List.copyOf(users)));
			for (int i = 0; i < users.size(); i++) {
				if (values.get(i) == null) {
					continue;
				}
				String value = String.valueOf(values.get(i));
				int sep = value.indexOf(':');
				backUpProcessor.applyFlushed(forecastId, Long.parseLong(users.get(i)),
						VoteChoice.valueOf(value.substring(0, sep)), Long.parseLong(value.substring(sep + 1)));
			}
			guarded(() -> redis.delete(flushingKey(forecastId, jobId)));
		} catch (RuntimeException e) {
			log.warn("flush 실패, dirty 복귀 — forecast {}", forecastId, e);
			guarded(() -> {
				Set<String> pending = redis.opsForSet().members(flushingKey(forecastId, jobId));
				if (pending != null && !pending.isEmpty()) {
					redis.opsForSet().add(dirtyKey(forecastId), pending.toArray(String[]::new));
				}
				redis.delete(flushingKey(forecastId, jobId));
				return null;
			});
		}
	}

	// 우회 → 정상 복귀 시 우회 기간의 DB 변경분을 Redis 에 seq 비교로 반영해
	// 집계가 낮게 보이는 구간을 닫는다.
	public void reconcile() {
		Instant since = bypassStartedAt;
		if (since == null) {
			return;
		}
		bypassStartedAt = null;
		try {
			int applied = 0;
			for (var vote : backUpProcessor.votesUpdatedAfter(since.minus(RECONCILE_MARGIN))) {
				Long result = guarded(() -> redis.execute(APPLY_SCRIPT,
						List.of(voteKey(vote.getForecastId()), aggKey(vote.getForecastId())),
						String.valueOf(vote.getUserId()), vote.getChoice().name(), String.valueOf(vote.getSeq())));
				if (result != null && result == 1) {
					applied++;
				}
			}
			log.info("복구 재조정 완료 — 반영 {}건", applied);
		} catch (RedisUnavailableException e) {
			bypassStartedAt = since;
			log.warn("복구 재조정 실패, 다음 복귀에 재시도", e);
		}
	}

	private ForecastStatus cachedState(long forecastId) {
		String state = guarded(() -> redis.opsForValue().get(stateKey(forecastId)));
		return state == null ? null : ForecastStatus.valueOf(state);
	}

	private void cacheState(long forecastId, ForecastStatus status) {
		guarded(() -> {
			redis.opsForValue().set(stateKey(forecastId), status.name(), STATE_TTL);
			return null;
		});
	}

	private <T> T guarded(Supplier<T> action) {
		try {
			return circuitBreaker.executeSupplier(action);
		} catch (CallNotPermittedException | DataAccessException e) {
			throw new RedisUnavailableException(e);
		}
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
}
