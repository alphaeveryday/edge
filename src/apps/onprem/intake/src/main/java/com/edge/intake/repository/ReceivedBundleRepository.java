package com.edge.intake.repository;

import com.edge.intake.entity.ReceivedBundle;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

/**
 * received_bundle — Raw Event Store. 수신 바이트 원본을 불변 보존한다.
 * dedup 은 cursor 기준(ON CONFLICT cursor_from DO NOTHING) — 재-Pull 응답은
 * bundle_id·generated_at 이 새로 발번돼 바이트가 달라지므로 바이트로 dedup 하지
 * 않는다(sync-protocol.md 전달 보장 절). ON CONFLICT 는 JPA save() 로 표현 불가라
 * native @Query 로 옮긴다 — 저장만 노출하려 Repository 마커를 상속한다.
 */
public interface ReceivedBundleRepository extends Repository<ReceivedBundle, Long> {

	/** @return 삽입된 행 수(0 = cursor_from 중복 skip, 무해 — at-least-once). */
	@Modifying
	@Transactional
	@Query(value = """
			INSERT INTO received_bundle (cursor_from, cursor_to, body)
			VALUES (:cursorFrom, :cursorTo, :body)
			ON CONFLICT (cursor_from) DO NOTHING
			""", nativeQuery = true)
	int save(@Param("cursorFrom") long cursorFrom, @Param("cursorTo") long cursorTo,
			@Param("body") byte[] body);
}
