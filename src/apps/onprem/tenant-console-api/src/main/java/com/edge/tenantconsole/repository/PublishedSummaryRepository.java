package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.PublicationEntity;
import org.springframework.data.repository.Repository;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

/**
 * publication 게시 문구 조회(ALPHA-607) — explanations 최종 문구(final)를 게시 원장에서
 * 읽는다. 검수 승인 재발행·사후 운영(PublicationRepository)이 writer 이고 이쪽은
 * reader 다(같은 엔티티, 관심사 분리). 목록 최종 문구는 항목당 개별 조회(N+1)를 피해
 * 배치로 읽는다.
 */
public interface PublishedSummaryRepository extends Repository<PublicationEntity, Long> {

	/**
	 * 항목들의 게시 이력 전부 — 상태 무관. 제공 중단(UNPUBLISHED)된 항목도 마지막 게시
	 * 문구(published_summary)를 final 로 보존해야 하므로 PUBLISHED 로 좁히지 않고, 서비스가
	 * 항목별 최신 게시본(publication_id 최대)을 고른다.
	 */
	List<PublicationEntity> findByAnalysisItemIdIn(Collection<String> analysisItemIds);

	/**
	 * 항목의 게시본(:status) 1건 — 최종 문구 정정(ALPHA-613)의 전값 스냅샷용. 게시 grain 상
	 * PUBLISHED 는 항목당 최대 1건이나, 방어적으로 최신본(publication_id 최대)을 취한다.
	 */
	Optional<PublicationEntity> findFirstByAnalysisItemIdAndStatusOrderByPublicationIdDesc(
			String analysisItemId, String status);
}
