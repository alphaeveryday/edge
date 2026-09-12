package com.edge.app.service;

import com.edge.app.dto.MyVoteResponse;
import com.edge.app.dto.VoteCountResponse;
import com.edge.app.entity.Forecast;
import com.edge.app.entity.ForecastStatus;
import com.edge.app.entity.VoteChoice;
import com.edge.app.error.AppErrorStatus;
import com.edge.app.repository.MemberRepository;
import com.edge.app.repository.VoteDistributedLockRepository;
import com.edge.app.repository.VoteRepository;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.List;

@Service
@RequiredArgsConstructor
public class VoteService {

	private static final Duration LOCK_TTL = Duration.ofMillis(500);

	private final VoteRepository voteRepository;
	private final VoteDistributedLockRepository voteDistributedLockRepository;
	private final VoteBackUpProcessor voteBackUpProcessor;
	private final ForecastService forecastService;
	private final MemberRepository memberRepository;

	public VoteCountResponse vote(long forecastId, long userId, VoteChoice choice) {
		if (!memberRepository.existsById(userId)) {
			throw new GeneralException(AppErrorStatus.MEMBER_NOT_FOUND);
		}
		if (forecastService.find(forecastId).getStatus() != ForecastStatus.OPEN) {
			throw new GeneralException(AppErrorStatus.FORECAST_NOT_OPEN);
		}
		// 락 실패 = TTL 내 연타 — 반영 없이 현재 집계만 반환한다
		if (!voteDistributedLockRepository.lock(forecastId, userId, LOCK_TTL)) {
			return voteRepository.read(forecastId);
		}
		VoteCountResponse counts = voteRepository.castVote(forecastId, userId, choice);
		voteBackUpProcessor.backUp(forecastId, userId, choice);
		return counts;
	}

	public VoteCountResponse counts(Forecast forecast) {
		if (forecast.getStatus() == ForecastStatus.OPEN) {
			return voteRepository.read(forecast.getId());
		}
		return voteBackUpProcessor.count(forecast.getId());
	}

	public List<MyVoteResponse> myVotes(long userId) {
		return voteBackUpProcessor.myVotes(userId);
	}
}
