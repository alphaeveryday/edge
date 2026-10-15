package com.edge.tenantconsole.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.OffsetDateTime;

/**
 * review_task 행 매핑(ALPHA-437) — 검수 결정·편집본·의견의 영속 기록. 현재 검수
 * 플로우는 배정(PENDING) 없이 결정 시점에 결정된 행을 직삽입한다(uq_review_task_open
 * 은 PENDING 만 제약). created_at 은 DB default(now())에 맡겨 매핑하지 않는다.
 * 편집본의 노출 경로는 publication.published_summary 스냅샷이다(DDL 주석).
 */
@Entity
@Table(name = "review_task")
public class ReviewTaskEntity {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	@Column(name = "review_task_id")
	private Long reviewTaskId;

	@Column(name = "analysis_item_id", nullable = false)
	private String analysisItemId;

	@Column(name = "status", nullable = false)
	private String status;

	@Column(name = "reviewer_id")
	private Long reviewerId;

	@Column(name = "edited_headline")
	private String editedHeadline;

	@Column(name = "edited_summary")
	private String editedSummary;

	@Column(name = "review_note")
	private String reviewNote;

	@Column(name = "decided_at")
	private OffsetDateTime decidedAt;

	protected ReviewTaskEntity() {
	}

	public ReviewTaskEntity(String analysisItemId, String status, Long reviewerId,
			String editedHeadline, String editedSummary, String reviewNote,
			OffsetDateTime decidedAt) {
		this.analysisItemId = analysisItemId;
		this.status = status;
		this.reviewerId = reviewerId;
		this.editedHeadline = editedHeadline;
		this.editedSummary = editedSummary;
		this.reviewNote = reviewNote;
		this.decidedAt = decidedAt;
	}

	public Long getReviewTaskId() {
		return reviewTaskId;
	}

	public String getAnalysisItemId() {
		return analysisItemId;
	}

	public String getStatus() {
		return status;
	}

	public Long getReviewerId() {
		return reviewerId;
	}

	public String getEditedHeadline() {
		return editedHeadline;
	}

	public String getEditedSummary() {
		return editedSummary;
	}

	public String getReviewNote() {
		return reviewNote;
	}

	public OffsetDateTime getDecidedAt() {
		return decidedAt;
	}
}
