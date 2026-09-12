package com.edge.app.service;

import com.edge.app.dto.MyVoteResponse;
import com.edge.app.dto.VoteCountResponse;
import com.edge.app.entity.Forecast;
import com.edge.app.entity.ForecastStatus;
import com.edge.app.entity.Vote;
import com.edge.app.entity.VoteChoice;
import com.edge.app.repository.ForecastRedisRepository;
import com.edge.app.repository.ForecastRepository;
import com.edge.app.repository.MemberRepository;
import com.edge.app.repository.RedisRebuildRepository;
import com.edge.app.repository.VoteRepository;
import com.edge.app.error.AppErrorStatus;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.dao.DataAccessException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class VoteService {

	private final ForecastRepository forecastRepository;
	private final VoteRepository voteRepository;
	private final MemberRepository memberRepository;
	private final RedisRebuildRepository redisRebuildRepository;
	private final ForecastRedisRepository forecastRedis;
	private final TransactionTemplate transaction;

	public VoteCountResponse vote(long forecastId, long userId, VoteChoice choice) {
		if (!memberRepository.existsById(userId)) {
			throw new GeneralException(AppErrorStatus.MEMBER_NOT_FOUND);
		}
		transaction.executeWithoutResult(status -> {
			forecastRepository.lockOpen(forecastId)
					.orElseThrow(() -> new GeneralException(AppErrorStatus.FORECAST_NOT_OPEN));
			voteRepository.upsert(forecastId, userId, choice.name());
		});
		try {
			forecastRedis.applyVote(forecastId, userId, choice);
			return new VoteCountResponse(
					forecastRedis.count(forecastId, VoteChoice.AGREE),
					forecastRedis.count(forecastId, VoteChoice.DISAGREE));
		} catch (DataAccessException e) {
			recordRebuild(forecastId, userId);
			return dbCounts(forecastId);
		}
	}

	public VoteCountResponse counts(Forecast forecast) {
		if (forecast.getStatus() == ForecastStatus.OPEN) {
			try {
				return new VoteCountResponse(
						forecastRedis.count(forecast.getId(), VoteChoice.AGREE),
						forecastRedis.count(forecast.getId(), VoteChoice.DISAGREE));
			} catch (DataAccessException ignored) {
			}
		}
		return dbCounts(forecast.getId());
	}

	public List<MyVoteResponse> myVotes(long userId) {
		List<Vote> votes = voteRepository.findByUserIdAndVoidedFalseOrderByUpdatedAtDesc(userId);
		List<Long> forecastIds = votes.stream().map(Vote::getForecastId).toList();
		Map<Long, Forecast> forecasts = forecastRepository.findAllById(forecastIds).stream()
				.collect(Collectors.toMap(Forecast::getId, Function.identity()));
		return votes.stream()
				.map(vote -> MyVoteResponse.from(vote, forecasts.get(vote.getForecastId())))
				.toList();
	}

	private VoteCountResponse dbCounts(long forecastId) {
		return new VoteCountResponse(
				voteRepository.countByForecastIdAndChoiceAndVoidedFalse(forecastId, VoteChoice.AGREE),
				voteRepository.countByForecastIdAndChoiceAndVoidedFalse(forecastId, VoteChoice.DISAGREE));
	}

	private void recordRebuild(long forecastId, long userId) {
		transaction.executeWithoutResult(status -> {
			redisRebuildRepository.record("forecast", String.valueOf(forecastId));
			redisRebuildRepository.record("user", String.valueOf(userId));
		});
	}
}
