package com.edge.app.service;

import com.edge.app.dto.MyVoteResponse;
import com.edge.app.dto.VoteCountResponse;
import com.edge.app.entity.Forecast;
import com.edge.app.entity.ForecastStatus;
import com.edge.app.entity.VoteChoice;
import com.edge.app.error.AppErrorStatus;
import com.edge.app.error.RedisUnavailableException;
import com.edge.app.repository.VoteRepository;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.util.List;

// 투표의 주 경로는 Redis(VoteRepository) — DB 가 필요한 일은 전부 VoteBackUpProcessor 로 보낸다.
@Service
@RequiredArgsConstructor
public class VoteService {

	private final VoteRepository voteRepository;
	private final VoteBackUpProcessor voteBackUpProcessor;

	// 정상: Redis Lua 선반영 후 200 (DB 반영은 flush 배치).
	// 우회: 브레이커 열림 시 BackUpProcessor 가 DB 직접 upsert.
	public VoteCountResponse vote(long forecastId, long userId, VoteChoice choice) {
		voteBackUpProcessor.requireMember(userId);
		try {
			ForecastStatus state = voteRepository.cachedState(forecastId);
			if (state == null) {
				state = voteBackUpProcessor.status(forecastId);
				voteRepository.cacheState(forecastId, state);
			}
			if (state != ForecastStatus.OPEN) {
				throw new GeneralException(AppErrorStatus.FORECAST_NOT_OPEN);
			}
			return voteRepository.vote(forecastId, userId, choice);
		} catch (RedisUnavailableException e) {
			voteBackUpProcessor.voteBypass(forecastId, userId, choice);
			return voteBackUpProcessor.dbCounts(forecastId);
		}
	}

	public VoteCountResponse counts(Forecast forecast) {
		if (forecast.getStatus() == ForecastStatus.OPEN) {
			try {
				return voteRepository.counts(forecast.getId());
			} catch (RedisUnavailableException ignored) {
			}
		}
		return voteBackUpProcessor.dbCounts(forecast.getId());
	}

	public List<MyVoteResponse> myVotes(long userId) {
		return voteBackUpProcessor.myVotes(userId);
	}

	public void markWithdrawn(long forecastId) {
		try {
			voteRepository.cacheState(forecastId, ForecastStatus.WITHDRAWN);
		} catch (RedisUnavailableException ignored) {
			// state 캐시 TTL(30s)이 지나면 DB 재조회로 수렴
		}
	}
}
