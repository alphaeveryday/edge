package com.edge.app.repository;

import com.edge.app.entity.Vote;
import com.edge.app.entity.VoteChoice;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface VoteRepository extends JpaRepository<Vote, Long> {

	@Modifying
	@Query(value = """
			insert into vote (forecast_id, user_id, choice, voided, updated_at)
			values (:forecastId, :userId, :choice, false, now())
			on conflict (forecast_id, user_id)
			do update set choice = excluded.choice, voided = false, updated_at = now()
			""", nativeQuery = true)
	void upsert(@Param("forecastId") long forecastId, @Param("userId") long userId, @Param("choice") String choice);

	long countByForecastIdAndChoiceAndVoidedFalse(long forecastId, VoteChoice choice);

	List<Vote> findByUserIdAndVoidedFalseOrderByUpdatedAtDesc(long userId);
}
