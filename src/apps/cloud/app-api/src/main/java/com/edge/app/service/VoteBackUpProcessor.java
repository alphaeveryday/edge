package com.edge.app.service;

import com.edge.app.dto.MyVoteResponse;
import com.edge.app.dto.VoteCountResponse;
import com.edge.app.entity.Forecast;
import com.edge.app.entity.ForecastStatus;
import com.edge.app.entity.Vote;
import com.edge.app.entity.VoteChoice;
import com.edge.app.error.AppErrorStatus;
import com.edge.app.error.RedisUnavailableException;
import com.edge.app.repository.ForecastRepository;
import com.edge.app.repository.MemberRepository;
import com.edge.app.repository.VoteBackUpRepository;
import com.edge.app.repository.VoteRepository;
import com.edge.common.exception.GeneralException;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;

// 투표 경로의 DB(백업 저장소) 처리 전담 — 배치 flush, 우회 쓰기, 복구 재조정, DB 집계.
@Slf4j
@Component
public class VoteBackUpProcessor {

	private static final int FLUSH_BATCH = 500;
	private static final Duration RECONCILE_MARGIN = Duration.ofSeconds(5);

	private final VoteRepository voteRepository;
	private final VoteBackUpRepository voteBackUpRepository;
	private final ForecastRepository forecastRepository;
	private final MemberRepository memberRepository;

	private volatile Instant bypassStartedAt;

	public VoteBackUpProcessor(VoteRepository voteRepository,
			VoteBackUpRepository voteBackUpRepository,
			ForecastRepository forecastRepository,
			MemberRepository memberRepository,
			CircuitBreaker redisCircuitBreaker) {
		this.voteRepository = voteRepository;
		this.voteBackUpRepository = voteBackUpRepository;
		this.forecastRepository = forecastRepository;
		this.memberRepository = memberRepository;
		redisCircuitBreaker.getEventPublisher().onStateTransition(event -> {
			switch (event.getStateTransition().getToState()) {
				case OPEN, FORCED_OPEN -> {
					if (bypassStartedAt == null) {
						bypassStartedAt = Instant.now();
					}
				}
				case CLOSED -> reconcile();
				default -> {
				}
			}
		});
	}

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
		voteBackUpRepository.upsertBypass(forecastId, userId, choice.name());
	}

	@Transactional(readOnly = true)
	public VoteCountResponse dbCounts(long forecastId) {
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

	// at-least-once flush: 리더 락 유실로 중복 실행돼도 seq 보호 upsert 가 no-op 으로 흡수.
	@Scheduled(fixedDelay = 3_000)
	public void flush() {
		try {
			if (!voteRepository.tryFlushLeader(Duration.ofSeconds(3))) {
				return;
			}
			for (String fid : voteRepository.dirtyForecasts()) {
				flushForecast(Long.parseLong(fid));
			}
		} catch (RedisUnavailableException ignored) {
			// Redis 불가면 flush 할 것도 없다 — 다음 주기 재시도
		}
	}

	private void flushForecast(long forecastId) {
		String jobId = UUID.randomUUID().toString();
		List<String> users = voteRepository.claimDirty(forecastId, jobId, FLUSH_BATCH);
		if (users.isEmpty()) {
			voteRepository.clearDirtyForecastIfDrained(forecastId);
			return;
		}
		try {
			List<Object> values = voteRepository.readVotes(forecastId, users);
			for (int i = 0; i < users.size(); i++) {
				if (values.get(i) == null) {
					continue;
				}
				String value = String.valueOf(values.get(i));
				int sep = value.indexOf(':');
				voteBackUpRepository.upsertIfNewer(forecastId, Long.parseLong(users.get(i)),
						value.substring(0, sep), Long.parseLong(value.substring(sep + 1)));
			}
			voteRepository.ackFlushing(forecastId, jobId);
		} catch (RuntimeException e) {
			log.warn("flush 실패, dirty 복귀 — forecast {}", forecastId, e);
			voteRepository.requeueFlushing(forecastId, jobId);
		}
	}

	// 우회 → 정상 복귀 시 우회 기간의 DB 변경분을 Redis 에 seq 비교로 반영해
	// 집계가 낮게 보이는 구간을 닫는다.
	public void reconcile() {
		Instant since = bypassStartedAt;
		if (since == null) {
			return;
		}
		bypassStartedAt = null;
		try {
			int applied = 0;
			for (Vote vote : voteBackUpRepository.findByUpdatedAtAfter(since.minus(RECONCILE_MARGIN))) {
				if (voteRepository.applyReconciled(vote.getForecastId(), vote.getUserId(),
						vote.getChoice(), vote.getSeq())) {
					applied++;
				}
			}
			log.info("복구 재조정 완료 — 반영 {}건", applied);
		} catch (RedisUnavailableException e) {
			bypassStartedAt = since;
			log.warn("복구 재조정 실패, 다음 복귀에 재시도", e);
		}
	}
}
