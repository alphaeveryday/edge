package com.edge.tenantconsole.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

/**
 * 콘솔 조작 감사 로그(console_action_log) — append-only. writer = tenant-console-api
 * (전유, 스키마 COMMENT). "누가 콘솔에서 무엇을 했나"의 원장으로, 사용자 관리
 * (ALPHA-119)·역할 부여(ALPHA-499) 등 상태 변경이 여기 기록된다. occurred_at 은
 * DB default 라 미매핑, detail 은 JSONB(타입 정합은 @JdbcTypeCode 로 validate).
 */
@Entity
@Table(name = "console_action_log")
public class ConsoleActionLogEntity {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	@Column(name = "console_action_log_id")
	private Long consoleActionLogId;

	@Column(name = "actor_id")
	private long actorId;

	private String action;

	@Column(name = "target_type")
	private String targetType;

	@Column(name = "target_id")
	private String targetId;

	/** JSONB — 서비스가 Jackson 으로 직렬화한 문자열을 넣는다. 이 매핑은 타입 정합 검증용. */
	@JdbcTypeCode(SqlTypes.JSON)
	private String detail;

	@Column(name = "client_ip")
	private String clientIp;

	protected ConsoleActionLogEntity() {
	}

	/** 신규 감사 레코드 — id·occurred_at 은 DB 가 채운다. */
	public ConsoleActionLogEntity(long actorId, String action, String targetType, String targetId,
			String detail, String clientIp) {
		this.actorId = actorId;
		this.action = action;
		this.targetType = targetType;
		this.targetId = targetId;
		this.detail = detail;
		this.clientIp = clientIp;
	}

	public Long getConsoleActionLogId() {
		return consoleActionLogId;
	}

	public long getActorId() {
		return actorId;
	}

	public String getAction() {
		return action;
	}

	public String getTargetType() {
		return targetType;
	}

	public String getTargetId() {
		return targetId;
	}

	public String getDetail() {
		return detail;
	}

	public String getClientIp() {
		return clientIp;
	}
}
