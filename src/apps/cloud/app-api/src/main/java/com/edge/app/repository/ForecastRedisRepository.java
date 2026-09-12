package com.edge.app.repository;

import com.edge.app.entity.VoteChoice;
import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.RedisOperations;
import org.springframework.data.redis.core.SessionCallback;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
@RequiredArgsConstructor
public class ForecastRedisRepository {

	private final StringRedisTemplate redis;

	public void markOpen(long forecastId) {
		redis.opsForValue().set(openKey(forecastId), "1");
	}

	public void clearOpen(long forecastId) {
		redis.delete(openKey(forecastId));
	}

	public void applyVote(long forecastId, long userId, VoteChoice choice) {
		String uid = String.valueOf(userId);
		String chosen = voteKey(forecastId, choice);
		String other = voteKey(forecastId, choice == VoteChoice.AGREE ? VoteChoice.DISAGREE : VoteChoice.AGREE);
		redis.execute(new SessionCallback<List<Object>>() {
			@Override
			@SuppressWarnings({"unchecked", "rawtypes"})
			public List<Object> execute(RedisOperations operations) {
				operations.multi();
				operations.opsForSet().remove(other, uid);
				operations.opsForSet().add(chosen, uid);
				operations.opsForSet().add(votedKey(userId), String.valueOf(forecastId));
				return operations.exec();
			}
		});
	}

	public long count(long forecastId, VoteChoice choice) {
		Long size = redis.opsForSet().size(voteKey(forecastId, choice));
		return size == null ? 0 : size;
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
