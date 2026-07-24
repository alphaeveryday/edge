package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.MemberEntity;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.util.Optional;

/**
 * member 원장 접근 — writer = tenant-console-api(전유, 스키마 COMMENT). 좁은
 * Repository 로 쓰는 연산만 노출한다(전체 CRUD·삭제 미노출 — 단일 writer 규율을
 * 구조적으로 강제). last_login 갱신은 단일 UPDATE 시맨틱을 위해 native @Modifying.
 */
public interface MemberRepository extends Repository<MemberEntity, Long> {

	Optional<MemberEntity> findByEmailAndActiveTrue(String email);

	@Query("SELECT count(m) FROM MemberEntity m")
	long count();

	/** 부트스트랩 데모 계정 시드(member 전유 writer). */
	MemberEntity save(MemberEntity member);

	@Transactional
	@Modifying
	@Query(value = "UPDATE member SET last_login_at = now() WHERE member_id = :id",
			nativeQuery = true)
	void touchLastLogin(@Param("id") long id);
}
