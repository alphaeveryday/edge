package com.edge.app.service;

import com.edge.app.entity.ForecastStatus;
import com.edge.app.entity.RedisRebuild;
import com.edge.app.entity.Vote;
import com.edge.app.entity.VoteChoice;
import com.edge.app.repository.ForecastRedisRepository;
import com.edge.app.repository.ForecastRepository;
import com.edge.app.repository.RedisRebuildRepository;
import com.edge.app.repository.VoteRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DataAccessException;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.List;

@Slf4j
@Service
@RequiredArgsConstructor
public class RebuildService {

	private final RedisRebuildRepository redisRebuildRepository;
	private final ForecastRepository forecastRepository;
	private final VoteRepository voteRepository;
	private final ForecastRedisRepository forecastRedis;
	private final TransactionTemplate transaction;

	@Scheduled(fixedDelay = 10_000)
	public void rebuildPending() {
		List<RedisRebuild> targets = redisRebuildRepository.findTop100ByStatusOrderByRequestedAtAsc("PENDING");
		for (RedisRebuild target : targets) {
			try {
				rebuild(target);
			} catch (DataAccessException e) {
				log.warn("재구축 실패, 다음 주기에 재시도 — {}:{}", target.getResourceType(), target.getResourceId(), e);
				return;
			}
			transaction.executeWithoutResult(status ->
					redisRebuildRepository.markDone(target.getId(), target.getRequestedAt()));
		}
	}

	private void rebuild(RedisRebuild target) {
		long id = Long.parseLong(target.getResourceId());
		if ("forecast".equals(target.getResourceType())) {
			boolean open = forecastRepository.findById(id)
					.map(forecast -> forecast.getStatus() == ForecastStatus.OPEN)
					.orElse(false);
			forecastRedis.rebuildForecast(id, open,
					uids(voteRepository.voterIds(id, VoteChoice.AGREE.name())),
					uids(voteRepository.voterIds(id, VoteChoice.DISAGREE.name())));
		} else {
			List<String> forecastIds = voteRepository.findByUserIdAndVoidedFalseOrderByUpdatedAtDesc(id).stream()
					.map(Vote::getForecastId)
					.map(String::valueOf)
					.toList();
			forecastRedis.rebuildUserVoted(id, forecastIds);
		}
	}

	private List<String> uids(List<Long> ids) {
		return ids.stream().map(String::valueOf).toList();
	}
}
