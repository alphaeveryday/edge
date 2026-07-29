package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.ServingScopeEntity;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Optional;

/**
 * serving_scope 제공 범위 토글 — writer = tenant-console-api(전유, 스키마 COMMENT).
 * 옵트아웃 모델(행 부재 = 기본 제공)이라 조회는 제외·재개 이력이 있는 키만 돌려주고,
 * 토글은 단일 upsert(ON CONFLICT flip)로 표현한다 — 첫 토글은 enabled=false 행을
 * 심고(기본 제공 → 제외), 이후 토글은 기존 값을 반전한다. 상위(MARKET) OFF 가 하위
 * (INSTRUMENT)에 우선하는 서빙 판정은 이 모듈 밖(publication-api)의 공유 규칙이다.
 */
public interface ServingScopeRepository extends Repository<ServingScopeEntity, Long> {

	List<ServingScopeEntity> findByScopeType(String scopeType);

	Optional<ServingScopeEntity> findByScopeTypeAndScopeKey(String scopeType, String scopeKey);

	/**
	 * 토글 upsert — 행이 없으면 enabled=false 로 심고(기본 제공에서 제외로), 있으면 반전.
	 * 단일 SQL 이라 동시 토글도 각자 원자적으로 반영된다(read-modify-write 경쟁 없음).
	 */
	@Transactional
	@Modifying
	@Query(value = """
			INSERT INTO serving_scope (scope_type, scope_key, enabled, updated_by, updated_at)
			VALUES (:scopeType, :scopeKey, false, :updatedBy, now())
			ON CONFLICT (scope_type, scope_key)
			DO UPDATE SET enabled = NOT serving_scope.enabled,
			              updated_by = :updatedBy, updated_at = now()
			""", nativeQuery = true)
	void toggle(@Param("scopeType") String scopeType, @Param("scopeKey") String scopeKey,
			@Param("updatedBy") long updatedBy);
}
