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
}
