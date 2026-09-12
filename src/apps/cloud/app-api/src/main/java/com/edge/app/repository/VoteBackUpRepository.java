package com.edge.app.repository;

import com.edge.app.entity.Vote;
import com.edge.app.entity.VoteChoice;
import com.edge.app.entity.VoteId;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;

public interface VoteBackUpRepository extends JpaRepository<Vote, VoteId> {

	// flush·재적용 공통 — seq 가 더 클 때만 갱신(at-least-once 중복 반영은 no-op)
	@Transactional
	@Modifying
	@Query(value = """
			insert into vote (forecast_id, user_id, choice, seq, updated_at)
			values (:forecastId, :userId, :choice, :seq, now())
			on conflict (forecast_id, user_id)
			do update set choice = excluded.choice, seq = excluded.seq, updated_at = now()
			where excluded.seq > vote.seq
			""", nativeQuery = true)
	void upsertIfNewer(@Param("forecastId") long forecastId, @Param("userId") long userId,
			@Param("choice") String choice, @Param("seq") long seq);

	// 우회 경로 — seq 는 DB 시계로 발번 (Redis–DB 시계 혼용 금지, 경로는 브레이커로 초 단위 분리)
	@Transactional
	@Modifying
	@Query(value = """
			insert into vote (forecast_id, user_id, choice, seq, updated_at)
			values (:forecastId, :userId, :choice,
					(extract(epoch from clock_timestamp()) * 1000000)::bigint, now())
			on conflict (forecast_id, user_id)
			do update set choice = excluded.choice, seq = excluded.seq, updated_at = now()
			where excluded.seq > vote.seq
			""", nativeQuery = true)
	void upsertBypass(@Param("forecastId") long forecastId, @Param("userId") long userId,
			@Param("choice") String choice);

	long countByForecastIdAndChoice(long forecastId, VoteChoice choice);

	List<Vote> findByUserIdOrderByUpdatedAtDesc(long userId);

	List<Vote> findByUpdatedAtAfter(Instant since);
}
