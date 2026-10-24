package com.edge.tenantsync.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

/** 종목 마스터 경계면(instrument) — 번들의 etf_ticker 출처. */
@Entity
@Table(name = "instrument")
@Immutable
public class Instrument {

	@Id
	private String instrumentId;

	private String ticker;

	protected Instrument() {
	}
}
