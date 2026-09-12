package com.edge.app.facade;

import com.edge.app.dto.MyVoteResponse;
import com.edge.app.dto.VoteCountResponse;
import com.edge.app.entity.VoteChoice;
import com.edge.app.error.AppErrorStatus;
import com.edge.app.error.RedisUnavailableException;
import com.edge.app.repository.ForecastRedisRepository;
import com.edge.app.repository.RedisRebuildRepository;
import com.edge.app.service.VoteService;
import com.edge.common.exception.GeneralException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.List;

@Component
public class VoteFacade {

	private final VoteService voteService;
	private final ForecastRedisRepository forecastRedis;
	private final RedisRebuildRepository redisRebuildRepository;
	private final boolean dedupeEnabled;
	private final Duration dedupeTtl;

	public VoteFacade(VoteService voteService,
			ForecastRedisRepository forecastRedis,
			RedisRebuildRepository redisRebuildRepository,
			@Value("${app.vote.dedupe-enabled:true}") boolean dedupeEnabled,
			@Value("${app.vote.dedupe-ttl:500ms}") Duration dedupeTtl) {
		this.voteService = voteService;
		this.forecastRedis = forecastRedis;
		this.redisRebuildRepository = redisRebuildRepository;
		this.dedupeEnabled = dedupeEnabled;
		this.dedupeTtl = dedupeTtl;
	}

	public VoteCountResponse vote(long forecastId, long userId, VoteChoice choice) {
		if (dedupeEnabled) {
			try {
				if (!forecastRedis.tryDedupe(forecastId, userId, dedupeTtl)) {
					throw new GeneralException(AppErrorStatus.VOTE_IN_PROGRESS);
				}
			} catch (RedisUnavailableException ignored) {
				// 저하 모드: 마커 생략 — Redis 파생 쓰기가 없으니 순서 역전 자체가 없다
			}
		}
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
