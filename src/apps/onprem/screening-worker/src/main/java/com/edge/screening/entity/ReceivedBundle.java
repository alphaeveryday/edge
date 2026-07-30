package com.edge.screening.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/**
 * 수신 번들(received_bundle) — Intake↔Screening 의 DB 매개 핸드오프. screening 은 미점검분의
 * cursor_from·body 만 읽으므로 그 둘만 매핑한다(스키마 SSOT=Flyway, ADR-0038). screened_at 마킹은
 * 리포지토리의 native @Query(UPDATE)로 나가고 필터도 SQL 에서 걸어 엔티티엔 매핑하지 않는다 —
 * 원본 컬럼(cursor·body)은 이 경계에서 불변이다.
 */
@Entity
@Table(name = "received_bundle")
public class ReceivedBundle {

	@Id
	private Long cursorFrom;

	private byte[] body;

	protected ReceivedBundle() {
	}

	public Long getCursorFrom() {
		return cursorFrom;
	}

	public byte[] getBody() {
		return body;
	}
}
