package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.ReviewTaskEntity;
import org.springframework.data.repository.Repository;

/**
 * review_task 기록 — writer 분담(스키마 COMMENT): 이 모듈은 생성·검수 결정
 * (APPROVED·EDITED_APPROVED·REJECTED)만 쓴다(CANCELLED 는 screening-worker).
 * 좁은 Repository 로 save 만 노출한다 — 결정 기록은 갱신·삭제하지 않는다.
 */
public interface ReviewTaskRepository extends Repository<ReviewTaskEntity, Long> {

	ReviewTaskEntity save(ReviewTaskEntity task);
}
