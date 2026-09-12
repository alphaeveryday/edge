package com.edge.app.repository;

import com.edge.app.entity.Vote;
import com.edge.app.entity.VoteChoice;
import com.edge.app.entity.VoteId;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface VoteBackUpRepository extends JpaRepository<Vote, VoteId> {

	@Modifying
	@Query(value = """
			insert into vote (forecast_id, user_id, choice, updated_at)
			values (:forecastId, :userId, :choice, now())
			on conflict (forecast_id, user_id)
			do update set choice = excluded.choice, updated_at = now()
			""", nativeQuery = true)
	void upsert(@Param("forecastId") Long forecastId, @Param("userId") Long userId,
			@Param("choice") String choice);

	long countByForecastIdAndChoice(long forecastId, VoteChoice choice);

	List<Vote> findByUserIdOrderByUpdatedAtDesc(long userId);
}
