package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.MemberEntity;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Optional;

/**
 * member 원장 접근 — writer = tenant-console-api(전유, 스키마 COMMENT). 좁은
 * Repository 로 쓰는·읽는 연산만 노출한다(하드 삭제 미노출 — 비활성화는 is_active
 * 토글, 단일 writer 규율을 구조적으로 강제). 상태 토글·last_login 갱신은 단일 UPDATE
 * 시맨틱을 위해 native @Modifying.
 */
public interface MemberRepository extends Repository<MemberEntity, Long> {

	Optional<MemberEntity> findByEmailAndActiveTrue(String email);

	/** 비활성화 대상 조회(ALPHA-119) — role·활성 상태 확인용. */
	Optional<MemberEntity> findById(Long id);

	/** 사용자 목록(ALPHA-119) — 등록 순(member_id) 오름차순, 비활성 포함 전체. */
	@Query("SELECT m FROM MemberEntity m ORDER BY m.memberId")
	List<MemberEntity> findAllOrderByMemberId();

	/**
	 * 활성 TENANT_ADMIN 행을 FOR UPDATE 로 잠그고 그 member_id 를 반환한다 — 마지막 관리자
	 * 판정과 비활성화를 직렬화한다(ALPHA-119). count 후 UPDATE 는 동시 비활성화가 둘 다
	 * count>1 을 보고 0명으로 만드는 TOCTOU 경쟁이 있어, 판정 전에 활성 관리자 집합을 잠근다.
	 * 반드시 호출부의 @Transactional 안에서 부른다(락은 트랜잭션 종료 시 풀린다).
	 */
	@Query(value = "SELECT member_id FROM member WHERE role = 'TENANT_ADMIN' AND is_active = true "
			+ "FOR UPDATE", nativeQuery = true)
	List<Long> lockActiveAdminIds();

	/** 등록 시 이메일 중복 사전 검사 — 정규화(소문자)된 이메일로 조회한다. */
	boolean existsByEmail(String email);

	@Query("SELECT count(m) FROM MemberEntity m")
	long count();

	/** 등록(member 전유 writer)·부트스트랩 데모 계정 시드. */
	MemberEntity save(MemberEntity member);

	/**
	 * 비활성화(ALPHA-119) — is_active=false 토글. 반환값은 영향 행 수로, 0 이면
	 * 대상 미존재(404)다. 이미 비활성인 대상도 멱등하게 1 을 반환한다(no-op).
	 */
	@Transactional
	@Modifying
	@Query(value = "UPDATE member SET is_active = false WHERE member_id = :id", nativeQuery = true)
	int deactivate(@Param("id") long id);

	/**
	 * 역할 변경(ALPHA-499) — role 단일 UPDATE. 반환값은 영향 행 수로, 0 이면 대상
	 * 미존재(404)다. role 어휘 검증은 서비스(400) + ck_member_role 제약이 이중 방어한다.
	 */
	@Transactional
	@Modifying
	@Query(value = "UPDATE member SET role = :role WHERE member_id = :id", nativeQuery = true)
	int updateRole(@Param("id") long id, @Param("role") String role);

	@Transactional
	@Modifying
	@Query(value = "UPDATE member SET last_login_at = now() WHERE member_id = :id",
			nativeQuery = true)
	void touchLastLogin(@Param("id") long id);
}
