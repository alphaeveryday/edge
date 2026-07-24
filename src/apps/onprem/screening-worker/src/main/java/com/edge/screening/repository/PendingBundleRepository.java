package com.edge.screening.repository;

import com.edge.screening.entity.ReceivedBundle;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * received_bundle 의 미점검분 조회·마킹 — Intake↔Screening 의 DB 매개 핸드오프.
 * 원본 컬럼(cursor·checksum·body)은 불변 — 이 저장소는 screened_at 만 갱신한다.
 * 조회·마킹만 노출하려 JpaRepository 가 아니라 Repository 마커를 상속한다. LIMIT·screened_at
 * 마킹은 native @Query 로 내려가고, 폴러가 쓰는 PendingBundle(cursor_from·body) 도메인 형상은
 * default 어댑터가 유지한다(호출부 무변경).
 */
public interface PendingBundleRepository extends Repository<ReceivedBundle, Long> {

	record PendingBundle(long cursorFrom, byte[] body) {
	}

	/** 미점검분을 cursor 순으로 최대 limit 건 — native LIMIT(엔티티는 cursor_from·body 만 매핑). */
	@Query(value = """
			SELECT cursor_from, body FROM received_bundle
			WHERE screened_at IS NULL ORDER BY cursor_from LIMIT :limit
			""", nativeQuery = true)
	List<ReceivedBundle> findUnscreenedRows(@Param("limit") int limit);

	/** 폴러 계약 유지 — 엔티티 행을 도메인 형상(cursor_from·body)으로 옮긴다. */
	default List<PendingBundle> findUnscreened(int limit) {
		return findUnscreenedRows(limit).stream()
				.map(row -> new PendingBundle(row.getCursorFrom(), row.getBody()))
				.toList();
	}

	/** 점검 완료 마킹(1회, NULL→now) — 점검 트랜잭션의 일부로 커밋된다(BundleScreener @Transactional). */
	@Modifying
	@Transactional
	@Query(value = "UPDATE received_bundle SET screened_at = now() WHERE cursor_from = :cursorFrom",
			nativeQuery = true)
	void markScreened(@Param("cursorFrom") long cursorFrom);
}
