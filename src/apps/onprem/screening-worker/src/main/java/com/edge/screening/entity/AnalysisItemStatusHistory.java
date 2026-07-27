package com.edge.screening.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/**
 * 상태 변경 이력(analysis_item_status_history) — writer 분담(스키마 COMMENT): 이 모듈은
 * 자기 소유 전이(자동 분기·Cloud 이벤트 반영)의 **SYSTEM 행**만 쓴다(actor_id NULL —
 * CHECK (actor_type='MEMBER')=(actor_id IS NOT NULL)). 검수 결정(MEMBER)은
 * tenant-console-api 몫. occurred_at 은 DB DEFAULT now() 소유라 매핑하지 않는다.
 */
@Entity
@Table(name = "analysis_item_status_history")
public class AnalysisItemStatusHistory {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	@Column(name = "status_history_id")
	private Long statusHistoryId;

	@Column(name = "analysis_item_id")
	private String analysisItemId;

	@Column(name = "from_status")
	private String fromStatus;

	@Column(name = "to_status")
	private String toStatus;

	@Column(name = "actor_type")
	private String actorType;

	@Column(name = "actor_id")
	private Long actorId;

	private String reason;

	protected AnalysisItemStatusHistory() {
	}

	/** SYSTEM 행 생성 — fromStatus NULL = 최초 진입(수신), reason 은 정정·무효화 사유. */
	public AnalysisItemStatusHistory(String analysisItemId, String fromStatus, String toStatus,
			String reason) {
		this.analysisItemId = analysisItemId;
		this.fromStatus = fromStatus;
		this.toStatus = toStatus;
		this.actorType = "SYSTEM";
		this.actorId = null;
		this.reason = reason;
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

	public String getReason() {
		return reason;
	}
}
