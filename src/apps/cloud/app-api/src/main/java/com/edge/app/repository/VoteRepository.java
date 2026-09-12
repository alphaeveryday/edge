package com.edge.app.repository;

import com.edge.app.dto.VoteCountResponse;
import com.edge.app.entity.VoteChoice;
import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Repository;

import java.util.List;

// 투표 주 저장소(Redis). 같은 유저의 read-modify-write 는 분산 락이 직렬화한다.
@Repository
@RequiredArgsConstructor
public class VoteRepository {

	// vote::forecast::{forecast_id}::votes      HASH  field=user, value=choice
	// vote::forecast::{forecast_id}::agg        HASH  field=choice, value=count
	private static final String VOTE_KEY_FORMAT = "vote::forecast::%s::votes";
	private static final String AGG_KEY_FORMAT = "vote::forecast::%s::agg";

	private final StringRedisTemplate redisTemplate;

	public VoteCountResponse castVote(Long forecastId, Long userId, VoteChoice choice) {
		String voteKey = generateVoteKey(forecastId);
		String aggKey = generateAggKey(forecastId);
		String old = (String) redisTemplate.opsForHash().get(voteKey, String.valueOf(userId));
		if (!choice.name().equals(old)) {
			redisTemplate.opsForHash().put(voteKey, String.valueOf(userId), choice.name());
			if (old != null) {
				redisTemplate.opsForHash().increment(aggKey, old, -1);
			}
			redisTemplate.opsForHash().increment(aggKey, choice.name(), 1);
		}
		return read(forecastId);
	}

	public VoteCountResponse read(Long forecastId) {
		List<Object> counts = redisTemplate.opsForHash()
				.multiGet(generateAggKey(forecastId), List.of("AGREE", "DISAGREE"));
		return new VoteCountResponse(toCount(counts.get(0)), toCount(counts.get(1)));
	}

	private long toCount(Object value) {
		return value == null ? 0L : Long.parseLong(String.valueOf(value));
	}

	private String generateVoteKey(Long forecastId) {
		return VOTE_KEY_FORMAT.formatted(forecastId);
	}

	private String generateAggKey(Long forecastId) {
		return AGG_KEY_FORMAT.formatted(forecastId);
	}
}
