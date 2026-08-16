package com.edge.publication.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/**
 * 요청 메트릭(serving_request_metric, ALPHA-501) — 요청 필터가 응답 완료 시점에
 * 기록하는 관측 원장(요청 수·상태·에러 코드). Dashboard(ALPHA-128) 집계의 데이터
 * 소스이자, Exposure Log 은퇴(ADR-0053) 후 서빙 경로에 남은 유일한 기록 축이다.
 * occurred_at 은 DB DEFAULT now() 에 맡기고 매핑하지 않는다(결측 없이 서버 시각).
 * 관측 용도라 고객 식별자·문구를 싣지 않는다.
 */
@Entity
@Table(name = "serving_request_metric")
public class ServingRequestMetric {

	@Id
	@GeneratedValue(strategy = GenerationType.IDENTITY)
	private Long requestMetricId;

	private String method;

	private String route;

	private short statusCode;

	private String errorCode;

	protected ServingRequestMetric() {
	}

	public ServingRequestMetric(String method, String route, short statusCode, String errorCode) {
		this.method = method;
		this.route = route;
		this.statusCode = statusCode;
		this.errorCode = errorCode;
	}

	public Long getRequestMetricId() {
		return requestMetricId;
	}

	public String getMethod() {
		return method;
	}

	public String getRoute() {
		return route;
	}

	public short getStatusCode() {
		return statusCode;
	}

	public String getErrorCode() {
		return errorCode;
	}
}
