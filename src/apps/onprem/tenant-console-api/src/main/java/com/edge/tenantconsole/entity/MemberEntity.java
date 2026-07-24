package com.edge.tenantconsole.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/**
 * member 원장 엔티티 — writer = tenant-console-api(전유, 스키마 COMMENT). 이 모듈이
 * 쓰는 컬럼만 부분 매핑한다(created_at 은 DB default 라 미매핑, last_login_at 은
 * native @Modifying 갱신이라 미매핑). DDL 은 Flyway 소유 — 매핑 검증(validate)·조회·
 * 부트스트랩 insert(save) 용.
 */
@Entity
@Table(name = "member")
public class MemberEntity {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	@Column(name = "member_id")
	private Long memberId;

	private String email;
	private String name;
	private String role;

	@Column(name = "is_active")
	private boolean active;

	@Column(name = "password_hash")
	private String passwordHash;

	protected MemberEntity() {
	}

	/** 부트스트랩 신규 계정 — member_id·created_at 은 DB 가 채운다. is_active=true 로 생성. */
	public MemberEntity(String email, String name, String role, String passwordHash) {
		this.email = email;
		this.name = name;
		this.role = role;
		this.active = true;
		this.passwordHash = passwordHash;
	}

	/** 테스트 픽스처용 — 실제 조회 인스턴스는 Hibernate 가 생성한다. */
	public MemberEntity(Long memberId, String email, String name, String role, boolean active,
			String passwordHash) {
		this.memberId = memberId;
		this.email = email;
		this.name = name;
		this.role = role;
		this.active = active;
		this.passwordHash = passwordHash;
	}

	public Long getMemberId() {
		return memberId;
	}

	public String getEmail() {
		return email;
	}

	public String getName() {
		return name;
	}

	public String getRole() {
		return role;
	}

	public boolean isActive() {
		return active;
	}

	public String getPasswordHash() {
		return passwordHash;
	}
}
