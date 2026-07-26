package com.edge.superadmin.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.Getter;

import java.time.OffsetDateTime;

/**
 * tenant 테이블 매핑(ALPHA-526). super-admin-api 가 테넌트 생성 표면(콘솔, ADR-0008)이라
 * 이 테이블에 행 read·write 를 한다(테이블별 단일 writer 의 형식 지정은 libs/schema
 * writer= 주석 후속). DDL 은 Flyway(libs/schema migrations-cloud)가 SSOT —
 * ddl-auto=validate 가 부팅 시 이 매핑을 실 테이블과 대조한다(Hibernate 는 DDL 미수정).
 * environment CHECK 는 대문자(POC/PROD, 레거시 DEV 는 수축 전 잠정) — IA 표기
 * (PoC/Production) 변환은 경계(dto·service)에서.
 * length 는 실 DDL(VARCHAR)에 맞춘다 — validate 는 길이를 강제하지 않으므로 계약을 명시한다.
 */
@Entity
@Table(name = "tenant")
@Getter
public class Tenant {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	@Column(name = "tenant_id")
	private Long id;

	@Column(name = "tenant_name", nullable = false, length = 100)
	private String name;

	@Column(name = "environment", nullable = false, length = 10)
	private String env;

	@Column(name = "status", nullable = false, length = 20)
	private String status;

	// 온보딩 기록(ALPHA-121) — 생성 폼의 초기 Tenant Admin·메모. 확장 전 행은 NULL.
	@Column(name = "initial_admin_name", length = 100)
	private String initialAdminName;

	@Column(name = "initial_admin_email", length = 255)
	private String initialAdminEmail;

	@Column(name = "memo")
	private String memo;

	@Column(name = "created_at", nullable = false)
	private OffsetDateTime createdAt;

	protected Tenant() {
		// JPA 전용
	}

	/** 신규 생성 — id 는 IDENTITY 로 DB 가 채운다. */
	public Tenant(String name, String env, String status, String initialAdminName,
			String initialAdminEmail, String memo, OffsetDateTime createdAt) {
		this.name = name;
		this.env = env;
		this.status = status;
		this.initialAdminName = initialAdminName;
		this.initialAdminEmail = initialAdminEmail;
		this.memo = memo;
		this.createdAt = createdAt;
	}

	/** 재구성(테스트 시드 등) — id 포함. */
	public Tenant(Long id, String name, String env, String status, String initialAdminName,
			String initialAdminEmail, String memo, OffsetDateTime createdAt) {
		this(name, env, status, initialAdminName, initialAdminEmail, memo, createdAt);
		this.id = id;
	}
}
