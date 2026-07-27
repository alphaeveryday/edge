package com.edge.screening.repository;

import com.edge.screening.entity.PolicyVersion;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;

import java.util.Optional;

/**
 * 활성 정책 버전 조회 — 활성(활성화됨·비활성 전) 행은 부분 유니크 인덱스가 최대 1건을
 * 보증한다(uq_policy_version_active). 0건 = 콘솔 온보딩 발행 전 — 호출부가 진행 중단한다.
 */
public interface PolicyRepository extends Repository<PolicyVersion, Long> {

	@Query("""
			SELECT p FROM PolicyVersion p
			WHERE p.activatedAt IS NOT NULL AND p.deactivatedAt IS NULL
			""")
	Optional<PolicyVersion> findActive();
}
