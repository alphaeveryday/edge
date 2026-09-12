package com.edge.app.repository;

import com.edge.app.entity.VoteChoice;
import com.edge.app.error.RedisUnavailableException;
import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import lombok.RequiredArgsConstructor;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.core.RedisOperations;
import org.springframework.data.redis.core.SessionCallback;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
@RequiredArgsConstructor
public class ForecastRedisRepository {

	private final StringRedisTemplate redis;
	private final CircuitBreaker circuitBreaker;

	public void markOpen(long forecastId) {
		guarded(() -> {
			redis.opsForValue().set(openKey(forecastId), "1");
			return null;
		});
	}

	public void clearOpen(long forecastId) {
		guarded(() -> redis.delete(openKey(forecastId)));
	}

	public void applyVote(long forecastId, long userId, VoteChoice choice) {
		String uid = String.valueOf(userId);
		String chosen = voteKey(forecastId, choice);
		String other = voteKey(forecastId, choice == VoteChoice.AGREE ? VoteChoice.DISAGREE : VoteChoice.AGREE);
		guarded(() -> redis.execute(new SessionCallback<List<Object>>() {
			@Override
			@SuppressWarnings({"unchecked", "rawtypes"})
			public List<Object> execute(RedisOperations operations) {
				operations.multi();
				operations.opsForSet().remove(other, uid);
				operations.opsForSet().add(chosen, uid);
				operations.opsForSet().add(votedKey(userId), String.valueOf(forecastId));
				return operations.exec();
			}
		}));
	}

	public void rebuildForecast(long forecastId, boolean open, List<String> agreeUids, List<String> disagreeUids) {
		guarded(() -> redis.execute(new SessionCallback<List<Object>>() {
			@Override
			@SuppressWarnings({"unchecked", "rawtypes"})
			public List<Object> execute(RedisOperations operations) {
				operations.multi();
				if (open) {
					operations.opsForValue().set(openKey(forecastId), "1");
				} else {
					operations.delete(openKey(forecastId));
				}
				swap(operations, voteKey(forecastId, VoteChoice.AGREE), agreeUids);
				swap(operations, voteKey(forecastId, VoteChoice.DISAGREE), disagreeUids);
				return operations.exec();
			}
		}));
	}

	public void rebuildUserVoted(long userId, List<String> forecastIds) {
		guarded(() -> redis.execute(new SessionCallback<List<Object>>() {
			@Override
			@SuppressWarnings({"unchecked", "rawtypes"})
			public List<Object> execute(RedisOperations operations) {
				operations.multi();
				swap(operations, votedKey(userId), forecastIds);
				return operations.exec();
			}
		}));
	}

	// 임시 키에 만들어 RENAME 으로 교체 — 재구축 중 부분 결과가 읽히지 않게 한다.
	@SuppressWarnings({"unchecked", "rawtypes"})
	private void swap(RedisOperations operations, String key, List<String> members) {
		if (members.isEmpty()) {
			operations.delete(key);
			return;
		}
		String tempKey = key + ":rebuild";
		operations.delete(tempKey);
		operations.opsForSet().add(tempKey, members.toArray(String[]::new));
		operations.rename(tempKey, key);
	}

	public long count(long forecastId, VoteChoice choice) {
		Long size = guarded(() -> redis.opsForSet().size(voteKey(forecastId, choice)));
		return size == null ? 0 : size;
	}

	private <T> T guarded(java.util.function.Supplier<T> action) {
		try {
			return circuitBreaker.executeSupplier(action);
		} catch (CallNotPermittedException | DataAccessException e) {
			throw new RedisUnavailableException(e);
		}
	}

	private String openKey(long forecastId) {
		return "forecast:" + forecastId + ":open";
	}

	private String voteKey(long forecastId, VoteChoice choice) {
		return "vote:" + forecastId + ":" + choice.name().toLowerCase();
	}

	private String votedKey(long userId) {
		return "user:" + userId + ":voted";
	}
}
