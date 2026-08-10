package com.edge.tenantsync.repository;

/**
 * 콘텐츠 기준시각 원료 프로젝션(ALPHA-918) — `stage_results->'window'` 의 창 끝
 * (KST "HH:MM" 문자열). timestamptz 합성·형식 검증은 BundleEntryStore 몫이다.
 */
public interface ContentWindowRow {

	String getExplanationResultId();

	String getHhmm();
}
