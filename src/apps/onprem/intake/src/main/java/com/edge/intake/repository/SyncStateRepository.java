package com.edge.intake.repository;

import com.edge.intake.entity.SyncState;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

/**
 * sync_state — committed cursor 의 durable 저장(단일 행). 권위 재개점은 이 값이다
 * (sync-protocol.md: 2모듈 표준에서 Pull 재개점 = Intake 의 committed cursor).
 * 읽기·전진만 노출하려 JpaRepository 가 아니라 Repository 마커를 상속한다. 전진은 단일 행
 * UPDATE 라 native @Query 로 내려간다(관리 엔티티 dirty checking 을 쓰지 않는다).
 */
public interface SyncStateRepository extends Repository<SyncState, Boolean> {

	/** 단일 행의 last_cursor. 행이 없으면 null — 계약상 baseline 이 단일 행을 보장한다. */
	@Query("SELECT s.lastCursor FROM SyncState s")
	Long findLastCursor();

	/** committed cursor. baseline 마이그레이션이 단일 행을 보장하므로 null 이면 fail-loud(Rule 12). */
	default long lastCursor() {
		Long cursor = findLastCursor();
		if (cursor == null) {
			throw new IllegalStateException("sync_state 가 비어 있다 — baseline 마이그레이션이 단일 행을 보장해야 한다");
		}
		return cursor;
	}

	/** committed cursor 전진(단일 행) — 적재 트랜잭션의 일부로 커밋된다(BundleIngestor @Transactional). */
	@Modifying
	@Transactional
	@Query(value = "UPDATE sync_state SET last_cursor = :cursorTo, last_synced_at = now()", nativeQuery = true)
	void advance(@Param("cursorTo") long cursorTo);
}
