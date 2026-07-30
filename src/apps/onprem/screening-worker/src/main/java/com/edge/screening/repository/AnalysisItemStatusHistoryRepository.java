package com.edge.screening.repository;

import com.edge.screening.entity.AnalysisItemStatusHistory;
import org.springframework.data.repository.Repository;

/**
 * analysis_item_status_history append — 이 모듈은 자기 소유 전이의 SYSTEM 행만 쓴다
 * (스키마 COMMENT 의 writer 분담, MEMBER 행은 tenant-console-api). 좁은 Repository 로
 * save 만 노출한다(append-only — 갱신·삭제·read 없음, 콘솔과 동일 관례).
 */
public interface AnalysisItemStatusHistoryRepository
		extends Repository<AnalysisItemStatusHistory, Long> {

	AnalysisItemStatusHistory save(AnalysisItemStatusHistory history);
}
