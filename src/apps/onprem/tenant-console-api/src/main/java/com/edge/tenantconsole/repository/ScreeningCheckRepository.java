package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.ScreeningCheckEntity;
import org.springframework.data.repository.Repository;

import java.util.Collection;
import java.util.List;

/**
 * screening_check 조회 — writer 는 screening-worker, 이 모듈은 검수 화면 reader 다
 * (ALPHA-436). 항목별 재현 조회(ix_screening_check_item)와 목록 사유 배치 조회만 노출한다.
 */
public interface ScreeningCheckRepository extends Repository<ScreeningCheckEntity, Long> {

	List<ScreeningCheckEntity> findByAnalysisItemIdOrderByScreeningCheckId(String analysisItemId);

	/** 목록 검수 사유 배치 조회 — 항목당 개별 조회(N+1)를 피한다. */
	List<ScreeningCheckEntity> findByAnalysisItemIdInAndResultOrderByScreeningCheckId(
			Collection<String> analysisItemIds, String result);

	/**
	 * explanations 사유 배치 조회(ALPHA-607) — 검수 대기(REVIEW)와 차단(BLOCK) 분기를
	 * 함께 읽는다. 차단 항목의 사유는 result='BLOCK' 행에 있어 REVIEW 만으로는 누락된다.
	 */
	List<ScreeningCheckEntity> findByAnalysisItemIdInAndResultInOrderByScreeningCheckId(
			Collection<String> analysisItemIds, Collection<String> results);
}
