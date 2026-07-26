package com.edge.tenantconsole.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/**
 * analysis_item_status_history 행 매핑(ALPHA-437) — append-only 상태 변경 이력.
 * writer 규약(스키마 COMMENT): 각 모듈은 자기가 만든 전이만 같은 트랜잭션에서 기록
 * — 이 모듈은 검수 결정(MEMBER) 전이만 쓴다. occurred_at 은 DB default(now()).
 */
@Entity
@Table(name = "analysis_item_status_history")
public class AnalysisItemStatusHistoryEntity {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	@Column(name = "status_history_id")
	private Long statusHistoryId;

	@Column(name = "analysis_item_id", nullable = false)
	private String analysisItemId;

	@Column(name = "from_status")
	private String fromStatus;

	@Column(name = "to_status", nullable = false)
	private String toStatus;

	@Column(name = "actor_type", nullable = false)
	private String actorType;

	@Column(name = "actor_id")
	private Long actorId;

	@Column(name = "reason")
	private String reason;

	protected AnalysisItemStatusHistoryEntity() {
	}

	public AnalysisItemStatusHistoryEntity(String analysisItemId, String fromStatus,
			String toStatus, String actorType, Long actorId, String reason) {
		this.analysisItemId = analysisItemId;
		this.fromStatus = fromStatus;
		this.toStatus = toStatus;
		this.actorType = actorType;
		this.actorId = actorId;
		this.reason = reason;
	}

	public Long getStatusHistoryId() {
		return statusHistoryId;
	}

	public String getAnalysisItemId() {
		return analysisItemId;
	}

	public String getFromStatus() {
		return fromStatus;
	}

	public String getToStatus() {
		return toStatus;
	}

	public String getActorType() {
		return actorType;
	}

	public Long getActorId() {
		return actorId;
	}

	public String getReason() {
		return reason;
	}
}
