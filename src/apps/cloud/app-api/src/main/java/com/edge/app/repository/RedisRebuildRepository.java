package com.edge.app.repository;

import com.edge.app.entity.RedisRebuild;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.List;

public interface RedisRebuildRepository extends JpaRepository<RedisRebuild, Long> {

	List<RedisRebuild> findTop100ByStatusOrderByRequestedAtAsc(String status);

	// requested_at 일치 조건: 처리 도중 새 실패가 같은 행을 PENDING 으로 되살렸으면 DONE 으로 덮지 않는다.
	@Modifying
	@Query(value = "update redis_rebuild set status = 'DONE' where id = :id and requested_at = :requestedAt",
			nativeQuery = true)
	void markDone(@Param("id") long id, @Param("requestedAt") Instant requestedAt);

	@Modifying
	@Query(value = """
			insert into redis_rebuild (resource_type, resource_id, status)
			values (:type, :id, 'PENDING')
			on conflict (resource_type, resource_id)
			do update set status = 'PENDING', requested_at = now()
			""", nativeQuery = true)
	void record(@Param("type") String type, @Param("id") String id);
}
