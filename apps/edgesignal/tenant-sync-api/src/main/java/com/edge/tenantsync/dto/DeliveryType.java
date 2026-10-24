package com.edge.tenantsync.dto;

/** 전달 레코드 유형 — docs/contracts/event-bundle-schema.md (CORRECTION 은 폐지 — ADR-0044). */
public enum DeliveryType {
	NEW,
	INVALIDATION
}
