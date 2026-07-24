package com.edge.publication.repository;

import com.edge.publication.entity.ExposureLog;
import org.springframework.data.repository.Repository;

/**
 * 노출 이력 기록 — 저장만 노출한다(읽기·삭제는 이 모듈의 관심사가 아니다). 조회=노출 기록은
 * 200 응답 경로에서만 일어난다(ADR-0013).
 */
public interface ExposureLogRepository extends Repository<ExposureLog, Long> {

	ExposureLog save(ExposureLog exposureLog);
}
