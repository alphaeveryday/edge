package com.edge.publication.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/**
 * 노출 이력(exposure_log) — 조회=노출(ADR-0013). publication-api 가 200 응답 시점에 기록하는
 * 유일한 쓰기 엔티티(@Immutable 아님). exposure_log_id 는 DB IDENTITY, exposed_at 은 DB
 * DEFAULT now() 에 맡기고 매핑하지 않는다(결측 없이 서버 시각으로 채워진다).
 */
@Entity
@Table(name = "exposure_log")
public class ExposureLog {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	private Long exposureLogId;

	private Long publicationId;

	private String customerHash;

	private String channel;

	private String summarySnapshot;

	protected ExposureLog() {
	}

	public ExposureLog(Long publicationId, String customerHash, String channel, String summarySnapshot) {
		this.publicationId = publicationId;
		this.customerHash = customerHash;
		this.channel = channel;
		this.summarySnapshot = summarySnapshot;
	}

	public Long getExposureLogId() {
		return exposureLogId;
	}

	public Long getPublicationId() {
		return publicationId;
	}

	public String getCustomerHash() {
		return customerHash;
	}

	public String getChannel() {
		return channel;
	}

	public String getSummarySnapshot() {
		return summarySnapshot;
	}
}
