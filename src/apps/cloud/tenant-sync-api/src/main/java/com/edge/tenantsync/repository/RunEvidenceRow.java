package com.edge.tenantsync.repository;

import java.time.Instant;

/**
 * 번들 evidences 네이티브 프로젝션 — lineage 두 갈래 UNION 의 문서 4컬럼 + 정렬 키.
 * documentId 는 와이어에 싣지 않고 정렬 동률 해소용으로만 존재한다(published_at 은
 * NULL 허용·비유일 — super-admin EVIDENCE_SQL 과 같은 이유).
 */
public interface RunEvidenceRow {

	String getExplanationRunId();

	String getDocumentId();

	String getDocumentType();

	String getTitle();

	String getSourceCode();

	Instant getPublishedAt();
}
