package com.edge.tenantsync.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

/** 결과를 낸 실행의 경계면(explanation_run) — 번들은 bundle_version 만 싣는다. */
@Entity
@Table(name = "explanation_run")
@Immutable
public class ExplanationRunEntity {

	@Id
	private String explanationRunId;

	private String bundleVersion;

	protected ExplanationRunEntity() {
	}
}
