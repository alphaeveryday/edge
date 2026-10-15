package com.edge.publication.repository;

import com.edge.publication.entity.PolicyVersionEntity;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;

import java.util.Optional;

/**
 * policy_version 활성 버전의 면책 문구 조회 — 이 모듈은 서빙 전용 <b>read-only reader</b> 다.
 * writer 는 tenant-console-api(전유, 스키마 COMMENT)이고, 여기서는 응답에 실을 문구만 읽는다.
 *
 * <p>활성 술어(activated_at IS NOT NULL AND deactivated_at IS NULL)는 콘솔 writer 의
 * {@code PolicyVersionRepository.findActive()} 를 <b>전사</b>한 것이다 — 같은 행을 가리켜야
 * 콘솔이 편집·표시하는 문구와 고객에게 나가는 문구가 어긋나지 않는다(ALPHA-772 의 본체).
 * 활성 1건 불변식은 부분 유니크 인덱스(uq_policy_version_active)가 arbiter 라 여기서
 * 재검사하지 않는다. 발행 전 0건 구간은 정상이며 호출부가 기본 문구로 수렴시킨다.
 */
public interface PolicyVersionRepository extends Repository<PolicyVersionEntity, Long> {

	@Query("""
			SELECT p.disclaimerText FROM PolicyVersionEntity p
			WHERE p.activatedAt IS NOT NULL AND p.deactivatedAt IS NULL
			""")
	Optional<String> findActiveDisclaimerText();
}
