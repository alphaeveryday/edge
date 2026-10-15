package com.edge.tenantconsole.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

import java.time.Instant;

/**
 * 요청 메트릭(serving_request_metric) 읽기 매핑 — writer 는 publication-api(요청 필터),
 * 이 모듈은 Dashboard 집계(ALPHA-128) reader 다(스키마 COMMENT 의 분담). @Immutable 로
 * dirty checking UPDATE 경로를 막아 단일 writer 분담을 코드로 강제한다. publication-api
 * 엔티티와 달리 occurred_at 을 매핑한다 — 집계 윈도 필터의 기준 컬럼이라서다.
 */
@Entity
@Immutable
@Table(name = "serving_request_metric")
public class ServingRequestMetricEntity {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	private Long requestMetricId;

	private String method;

	private String route;

	private short statusCode;

	private String errorCode;

	private Instant occurredAt;

	protected ServingRequestMetricEntity() {
	}
}
