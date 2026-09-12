package com.edge.app.service;

import com.edge.app.dto.MyVoteResponse;
import com.edge.app.dto.VoteCountResponse;
import com.edge.app.entity.Forecast;
import com.edge.app.entity.Vote;
import com.edge.app.entity.VoteChoice;
import com.edge.app.error.AppErrorStatus;
import com.edge.app.repository.ForecastRepository;
import com.edge.app.repository.MemberRepository;
import com.edge.app.repository.VoteRepository;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

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

	@Transactional
	public void vote(long forecastId, long userId, VoteChoice choice) {
		if (!memberRepository.existsById(userId)) {
			throw new GeneralException(AppErrorStatus.MEMBER_NOT_FOUND);
		}
		forecastRepository.lockOpen(forecastId)
				.orElseThrow(() -> new GeneralException(AppErrorStatus.FORECAST_NOT_OPEN));
		voteRepository.upsert(forecastId, userId, choice.name());
	}

	@Transactional(readOnly = true)
	public VoteCountResponse dbCounts(long forecastId) {
		return new VoteCountResponse(
				voteRepository.countByForecastIdAndChoiceAndVoidedFalse(forecastId, VoteChoice.AGREE),
				voteRepository.countByForecastIdAndChoiceAndVoidedFalse(forecastId, VoteChoice.DISAGREE));
	}

	@Transactional(readOnly = true)
	public List<MyVoteResponse> myVotes(long userId) {
		List<Vote> votes = voteRepository.findByUserIdAndVoidedFalseOrderByUpdatedAtDesc(userId);
		List<Long> forecastIds = votes.stream().map(Vote::getForecastId).toList();
		Map<Long, Forecast> forecasts = forecastRepository.findAllById(forecastIds).stream()
				.collect(Collectors.toMap(Forecast::getId, Function.identity()));
		return votes.stream()
				.map(vote -> MyVoteResponse.from(vote, forecasts.get(vote.getForecastId())))
				.toList();
	}
}
