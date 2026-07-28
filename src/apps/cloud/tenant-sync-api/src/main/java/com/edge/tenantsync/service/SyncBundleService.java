package com.edge.tenantsync.service;

import com.edge.tenantsync.dto.BundleEntry;
import com.edge.tenantsync.dto.EventBundle;
import com.edge.tenantsync.repository.BundleEntryStore;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Optional;

/**
 * Pull 오케스트레이션: 전달 레코드 조회 → 번들 조립. 직렬화·봉투 씌우기는 상위(컨트롤러/
 * Spring MessageConverter) 소관이다. 반환이 empty 면 신규 없음 — HTTP 표현(204)은 컨트롤러 소관.
 */
@Service
public class SyncBundleService {

	private final BundleEntryStore bundleEntryStore;

	public SyncBundleService(BundleEntryStore bundleEntryStore) {
		this.bundleEntryStore = bundleEntryStore;
	}

	public Optional<EventBundle> pull(long tenantId, long afterCursor, int limit) {
		List<BundleEntry> entries = bundleEntryStore.findAfter(tenantId, afterCursor, limit);
		if (entries.isEmpty()) {
			return Optional.empty();
		}
		return Optional.of(EventBundle.of(tenantId, entries));
	}
}
