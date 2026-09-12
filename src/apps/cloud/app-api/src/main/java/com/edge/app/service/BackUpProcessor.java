package com.edge.app.service;

import com.edge.app.dto.MyVoteResponse;
import com.edge.app.dto.VoteCountResponse;
import com.edge.app.entity.Forecast;
import com.edge.app.entity.ForecastStatus;
import com.edge.app.entity.Vote;
import com.edge.app.entity.VoteChoice;
import com.edge.app.error.AppErrorStatus;
import com.edge.app.repository.ForecastRepository;
import com.edge.app.repository.MemberRepository;
import com.edge.app.repository.VoteRepository;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

// 투표 경로의 DB 접근 전담 — 우회 쓰기, flush 반영, 복구 재조정 조회, DB 집계.
@Component
@RequiredArgsConstructor
public class BackUpProcessor {

	private final ForecastRepository forecastRepository;
	private final VoteRepository voteRepository;
	private final MemberRepository memberRepository;

	public void requireMember(long userId) {
		if (!memberRepository.existsById(userId)) {
			throw new GeneralException(AppErrorStatus.MEMBER_NOT_FOUND);
		}
	}

	@Transactional(readOnly = true)
	public ForecastStatus status(long forecastId) {
		return forecastRepository.findById(forecastId)
				.map(Forecast::getStatus)
				.orElseThrow(() -> new GeneralException(AppErrorStatus.FORECAST_NOT_FOUND));
	}

	// 우회 경로 — seq 는 DB 시계로 발번 (Redis 시계와 혼용 금지, 경로는 브레이커로 초 단위 분리)
	@Transactional
	public void voteBypass(long forecastId, long userId, VoteChoice choice) {
		if (status(forecastId) != ForecastStatus.OPEN) {
			throw new GeneralException(AppErrorStatus.FORECAST_NOT_OPEN);
		}
		voteRepository.upsertBypass(forecastId, userId, choice.name());
	}

	// flush·재적용 — seq 가 더 클 때만 갱신 (at-least-once 중복은 no-op)
	public void applyFlushed(long forecastId, long userId, VoteChoice choice, long seq) {
		voteRepository.upsertIfNewer(forecastId, userId, choice.name(), seq);
	}

	@Transactional(readOnly = true)
	public VoteCountResponse dbCounts(long forecastId) {
		return new VoteCountResponse(
				voteRepository.countByForecastIdAndChoice(forecastId, VoteChoice.AGREE),
				voteRepository.countByForecastIdAndChoice(forecastId, VoteChoice.DISAGREE));
	}

	@Transactional(readOnly = true)
	public List<Vote> votesUpdatedAfter(Instant since) {
		return voteRepository.findByUpdatedAtAfter(since);
	}

	@Transactional(readOnly = true)
	public List<MyVoteResponse> myVotes(long userId) {
		List<Vote> votes = voteRepository.findByUserIdOrderByUpdatedAtDesc(userId);
		List<Long> forecastIds = votes.stream().map(Vote::getForecastId).toList();
		Map<Long, Forecast> forecasts = forecastRepository.findAllById(forecastIds).stream()
				.collect(Collectors.toMap(Forecast::getId, Function.identity()));
		return votes.stream()
				.map(vote -> MyVoteResponse.from(vote, forecasts.get(vote.getForecastId())))
				.toList();
	}
}
