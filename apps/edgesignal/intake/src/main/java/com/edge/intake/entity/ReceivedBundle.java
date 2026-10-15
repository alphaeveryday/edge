package com.edge.intake.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/**
 * 수신 번들(received_bundle) — Raw Event Store. intake 가 쓰는 컬럼(cursor_from PK·cursor_to·
 * body)만 매핑한다(스키마 SSOT=Flyway, ADR-0038). received_at·screened_at 은 이 모듈의
 * 관심사가 아니라 미매핑(DB DEFAULT·타 모듈 소관). 적재는 리포지토리의 native @Query(ON CONFLICT
 * DO NOTHING, cursor dedup)로 나가므로 @Immutable 은 붙이지 않는다.
 */
@Entity
@Table(name = "received_bundle")
public class ReceivedBundle {

	@Id
	private Long cursorFrom;

	private Long cursorTo;

	private byte[] body;

	protected ReceivedBundle() {
	}

	public Long getCursorFrom() {
		return cursorFrom;
	}

	public Long getCursorTo() {
		return cursorTo;
	}

	public byte[] getBody() {
		return body;
	}
}
