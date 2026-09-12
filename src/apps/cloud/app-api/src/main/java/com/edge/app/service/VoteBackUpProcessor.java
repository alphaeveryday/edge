package com.edge.app.service;

import com.edge.app.dto.MyVoteResponse;
import com.edge.app.dto.VoteCountResponse;
import com.edge.app.entity.Forecast;
import com.edge.app.entity.Vote;
import com.edge.app.entity.VoteChoice;
import com.edge.app.repository.ForecastRepository;
import com.edge.app.repository.VoteBackUpRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

// 투표의 DB(백업 저장소) 처리 전담.
@Component
@RequiredArgsConstructor
public class VoteBackUpProcessor {

	private final VoteBackUpRepository voteBackUpRepository;
	private final ForecastRepository forecastRepository;

	@Transactional
	public void backUp(Long forecastId, Long userId, VoteChoice choice) {
		voteBackUpRepository.upsert(forecastId, userId, choice.name());
	}

	@Transactional(readOnly = true)
	public VoteCountResponse count(long forecastId) {
		return new VoteCountResponse(
				voteBackUpRepository.countByForecastIdAndChoice(forecastId, VoteChoice.AGREE),
				voteBackUpRepository.countByForecastIdAndChoice(forecastId, VoteChoice.DISAGREE));
	}

	@Transactional(readOnly = true)
	public List<MyVoteResponse> myVotes(long userId) {
		List<Vote> votes = voteBackUpRepository.findByUserIdOrderByUpdatedAtDesc(userId);
		List<Long> forecastIds = votes.stream().map(Vote::getForecastId).toList();
		Map<Long, Forecast> forecasts = forecastRepository.findAllById(forecastIds).stream()
				.collect(Collectors.toMap(Forecast::getId, Function.identity()));
		return votes.stream()
				.map(vote -> MyVoteResponse.from(vote, forecasts.get(vote.getForecastId())))
				.toList();
	}
}
