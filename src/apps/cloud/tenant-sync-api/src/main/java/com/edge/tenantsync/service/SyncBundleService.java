package com.edge.tenantsync.service;

import com.edge.tenantsync.dto.BundleEntry;
import com.edge.tenantsync.dto.EventBundle;
import com.edge.tenantsync.repository.BundleEntryStore;
import com.edge.tenantsync.service.BundleSerializer.SerializedBundle;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Optional;

/**
 * Pull 오케스트레이션: 전달 레코드 조회 → 번들 조립 → 직렬화+체크섬.
 * 반환이 empty 면 신규 없음 — HTTP 표현(204)은 컨트롤러 소관.
 */
@Service
public class SyncBundleService {

	private final BundleEntryStore bundleEntryStore;
	private final BundleSerializer serializer;

	public SyncBundleService(BundleEntryStore bundleEntryStore, BundleSerializer serializer) {
		this.bundleEntryStore = bundleEntryStore;
		this.serializer = serializer;
	}

	public Optional<SerializedBundle> pull(long tenantId, long afterCursor, int limit) {
		List<BundleEntry> entries = bundleEntryStore.findAfter(tenantId, afterCursor, limit);
		if (entries.isEmpty()) {
			return Optional.empty();
		}
		return Optional.of(serializer.serialize(EventBundle.of(tenantId, entries)));
	}
}
