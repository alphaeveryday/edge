package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.AnalysisItemStatusHistoryEntity;
import org.springframework.data.repository.Repository;

/**
 * analysis_item_status_history append — writer 분담(스키마 COMMENT): 이 모듈은 검수
 * 결정(MEMBER) 전이만, 자동 분기·Cloud 이벤트(SYSTEM)는 screening-worker 가 쓴다.
 * 좁은 Repository 로 save 만 노출한다(append-only — 갱신·삭제 없음).
 */
public interface AnalysisItemStatusHistoryRepository
		extends Repository<AnalysisItemStatusHistoryEntity, Long> {

	AnalysisItemStatusHistoryEntity save(AnalysisItemStatusHistoryEntity history);
}
