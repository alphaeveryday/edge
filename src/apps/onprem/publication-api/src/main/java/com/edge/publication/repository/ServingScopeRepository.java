package com.edge.publication.repository;

import com.edge.publication.entity.ServingScopeEntity;
import org.springframework.data.repository.Repository;

import java.util.Optional;

/**
 * serving_scope 제공 범위 토글 조회 — 이 모듈은 서빙 판정 전용 <b>read-only reader</b> 다.
 * writer 는 tenant-console-api(전유, 스키마 COMMENT)이고, 여기서는 요청 종목의 차단 여부를
 * PK(scope_type·scope_key) 룩업으로만 읽는다. 옵트아웃 모델이라 행 부재 = 기본 제공이며,
 * 상위(MARKET) OFF 가 하위(INSTRUMENT)에 우선하는 판정은 {@link
 * com.edge.publication.service.ExplanationService} 의 공유 규칙이다.
 */
public interface ServingScopeRepository extends Repository<ServingScopeEntity, Long> {

	Optional<ServingScopeEntity> findByScopeTypeAndScopeKey(String scopeType, String scopeKey);
}
