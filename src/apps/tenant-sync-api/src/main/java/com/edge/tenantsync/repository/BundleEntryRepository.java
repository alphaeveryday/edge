package com.edge.tenantsync.repository;

import com.edge.tenantsync.dto.BundleEntry;

import java.util.List;

/**
 * 테넌트별 전달 레코드를 cursor 순으로 읽는다 — 번들 생성의 유일한 소스.
 * 전달 레코드의 저장 구조·fan-out 은 영서 고도화 영역이라 이 인터페이스는 형상을
 * 전제하지 않는다. 현재는 인메모리 스텁 — 저장 설계 확정 후 실구현으로 교체.
 */
public interface BundleEntryRepository {

	/** {@code afterCursor} 초과분을 cursor 오름차순으로 최대 {@code limit} 건 반환한다. */
	List<BundleEntry> findAfter(long tenantId, long afterCursor, int limit);
}
