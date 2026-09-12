package com.edge.app.facade;

import com.edge.app.dto.MyVoteResponse;
import com.edge.app.dto.VoteCountResponse;
import com.edge.app.entity.VoteChoice;
import com.edge.app.error.RedisUnavailableException;
import com.edge.app.repository.ForecastRedisRepository;
import com.edge.app.repository.RedisRebuildRepository;
import com.edge.app.service.VoteService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
@RequiredArgsConstructor
public class VoteFacade {

	private final VoteService voteService;
	private final ForecastRedisRepository forecastRedis;
	private final RedisRebuildRepository redisRebuildRepository;

	public VoteCountResponse vote(long forecastId, long userId, VoteChoice choice) {
		voteService.vote(forecastId, userId, choice);
		try {
			forecastRedis.applyVote(forecastId, userId, choice);
			return new VoteCountResponse(
					forecastRedis.count(forecastId, VoteChoice.AGREE),
					forecastRedis.count(forecastId, VoteChoice.DISAGREE));
		} catch (RedisUnavailableException e) {
			redisRebuildRepository.record("forecast", String.valueOf(forecastId));
			redisRebuildRepository.record("user", String.valueOf(userId));
			return voteService.dbCounts(forecastId);
		}
	}

	public List<MyVoteResponse> myVotes(long userId) {
		return voteService.myVotes(userId);
	}
}
