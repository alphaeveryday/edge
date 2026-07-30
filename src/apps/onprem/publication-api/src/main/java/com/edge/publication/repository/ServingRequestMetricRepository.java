package com.edge.publication.repository;

import com.edge.publication.entity.ServingRequestMetric;
import org.springframework.data.repository.Repository;

/**
 * serving_request_metric append — writer = publication-api(요청 필터), reader =
 * tenant-console-api(Dashboard 집계, ALPHA-128 — 스키마 COMMENT). 좁은 Repository
 * 로 save 만 노출한다(append-only 관측 원장 — 갱신·삭제 없음).
 */
public interface ServingRequestMetricRepository extends Repository<ServingRequestMetric, Long> {

	ServingRequestMetric save(ServingRequestMetric metric);
}
