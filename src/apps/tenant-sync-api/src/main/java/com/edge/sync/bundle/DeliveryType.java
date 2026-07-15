package com.edge.sync.bundle;

/** outbox 전달 레코드 유형 — docs/contracts/event-bundle-schema.md. */
public enum DeliveryType {
	NEW,
	CORRECTION,
	INVALIDATION
}
