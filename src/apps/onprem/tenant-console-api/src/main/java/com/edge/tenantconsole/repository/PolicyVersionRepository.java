package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.PolicyVersionEntity;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

/**
 * policy_version 발행·조회 — writer 는 이 모듈(스키마 COMMENT). 버전 불변(ADR-0018)이라
 * 쓰기는 발행 INSERT(save)와 비활성 전이(deactivate)뿐이다. 활성 1건 불변식은 부분
 * 유니크 인덱스가 arbiter — 발행 경합은 제약 위반으로 드러난다(코드로 재검사하지 않는다).
 */
public interface PolicyVersionRepository extends Repository<PolicyVersionEntity, Long> {

	@Query("""
			SELECT p FROM PolicyVersionEntity p
			WHERE p.activatedAt IS NOT NULL AND p.deactivatedAt IS NULL
			""")
	Optional<PolicyVersionEntity> findActive();

	@Query("SELECT COALESCE(MAX(p.versionNo), 0) FROM PolicyVersionEntity p")
	int maxVersionNo();

	/** 활성 종결 전이 — 신규 발행과 같은 트랜잭션에서 먼저 실행돼야 활성 arbiter 를 통과한다. */
	@Modifying
	@Query("""
			UPDATE PolicyVersionEntity p SET p.deactivatedAt = CURRENT_TIMESTAMP
			WHERE p.policyVersionId = :id AND p.deactivatedAt IS NULL
			""")
	int deactivate(@Param("id") long id);

	PolicyVersionEntity save(PolicyVersionEntity version);

	List<PolicyVersionEntity> findAllByOrderByVersionNoDesc();

	/** 검사 행이 가리키는 판정 당시 버전들 — 행마다 findById 를 부르면 N+1 이다. */
	List<PolicyVersionEntity> findByPolicyVersionIdIn(Collection<Long> policyVersionIds);
}
