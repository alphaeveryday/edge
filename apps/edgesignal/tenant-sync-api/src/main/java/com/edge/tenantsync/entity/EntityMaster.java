package com.edge.tenantsync.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

/**
 * 엔터티 마스터 경계면(entity 테이블) — 번들의 etf_name(display_name) 출처.
 * 클래스명은 jakarta @Entity 와의 충돌을 피해 시드 마이그레이션의 "엔터티 마스터" 명칭을 차용.
 * instrument 와 같은 ID 값을 공유한다(entity_id = instrument_id — ADR-0027 서브타입 설계).
 */
@Entity
@Table(name = "entity")
@Immutable
public class EntityMaster {

	@Id
	private String entityId;

	private String displayName;

	protected EntityMaster() {
	}
}
