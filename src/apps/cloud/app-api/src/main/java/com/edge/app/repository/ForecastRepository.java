package com.edge.app.repository;

import com.edge.app.entity.Forecast;
import com.edge.app.entity.ForecastStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface ForecastRepository extends JpaRepository<Forecast, Long> {

	@Query(value = "select id from forecast where id = :id and status = 'OPEN' for share", nativeQuery = true)
	Optional<Long> lockOpen(@Param("id") long id);

	@Modifying
	@Query(value = """
			update forecast set status = 'WITHDRAWN', withdraw_reason = :reason
			where id = :id and status in ('OPEN', 'CLOSED')
			""", nativeQuery = true)
	int withdraw(@Param("id") long id, @Param("reason") String reason);

	@Query(value = "update forecast set status = 'CLOSED' where status = 'OPEN' and end_at < now() returning id",
			nativeQuery = true)
	List<Long> closeExpired();

	List<Forecast> findByStatusOrderByCreatedAtDesc(ForecastStatus status);
}
