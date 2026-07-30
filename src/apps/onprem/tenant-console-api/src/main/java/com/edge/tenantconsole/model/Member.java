package com.edge.tenantconsole.model;

import com.edge.tenantconsole.entity.MemberEntity;

import java.time.OffsetDateTime;

/**
 * member 도메인 표현 — 서비스가 소비하는 계층(영속 엔티티와 분리). 리포지토리가 반환한
 * MemberEntity 를 서비스가 이 record 로 매핑한다(엔티티는 영속 계층에 머문다).
 */
public record Member(
		long memberId,
		String email,
		String name,
		String role,
		boolean active,
		String passwordHash,
		OffsetDateTime lastLoginAt
) {
	public static Member from(MemberEntity e) {
		return new Member(e.getMemberId(), e.getEmail(), e.getName(), e.getRole(), e.isActive(),
				e.getPasswordHash(), e.getLastLoginAt());
	}
}
