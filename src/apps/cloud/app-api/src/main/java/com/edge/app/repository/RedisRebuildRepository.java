package com.edge.app.repository;

import com.edge.app.entity.RedisRebuild;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface RedisRebuildRepository extends JpaRepository<RedisRebuild, Long> {

	@Modifying
	@Query(value = """
			insert into redis_rebuild (resource_type, resource_id, status)
			values (:type, :id, 'PENDING')
			on conflict (resource_type, resource_id)
			do update set status = 'PENDING', requested_at = now()
			""", nativeQuery = true)
	void record(@Param("type") String type, @Param("id") String id);
}
