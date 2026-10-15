package com.edge.tenantsync.repository;

import java.time.LocalDate;

/** 번들 source_events 네이티브 프로젝션 — source_event 경계면 4컬럼 + 런 키. */
public interface RunSourceEventRow {

	String getExplanationRunId();

	String getSourceEventId();

	String getSourceClass();

	String getEventTypeCode();

	LocalDate getEventDate();
}
