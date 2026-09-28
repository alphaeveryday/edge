package com.edge.app.member.repository;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.member.entity.Principal;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

/**
 * 요청의 principal(회원 또는 디바이스)을 principal.id 로 해소한다. 처음 보는 디바이스는 device·principal 행을 만든다.
 * 다른 도메인이 이 저장소를 읽는 유일한 목적이 이 해소다. 로그인 매핑(erd.md principal 절)도 여기 둔다.
 * 관심·온보딩·알림 테이블을 직접 UPDATE 하는 이유는 소유자 이전이 principal 의 규칙이기 때문이다.
 */
public interface PrincipalRepository extends JpaRepository<Principal, Long> {
    Optional<Principal> findByMemberId(long memberId);

    Optional<Principal> findByDeviceId(long deviceId);

    // 한 문장으로 device 와 principal 을 upsert 한다. no-op update 는 기존 행의 id 를 RETURNING 하기 위한 것.
    @Query(value = """
            with d as (
                insert into device(device_key) values (:key)
                on conflict (device_key) do update set last_seen_at = now() returning id)
            insert into principal(kind, device_id) select 'device', id from d
            on conflict (device_id) do update set kind = excluded.kind returning id
            """, nativeQuery = true)
    Long upsertDevice(@Param("key") String deviceKey);

    @Query(value = """
            insert into principal(kind, member_id) values ('member', :member)
            on conflict (member_id) do update set kind = excluded.kind returning id
            """, nativeQuery = true)
    Long upsertMember(@Param("member") long memberId);

    default long resolve(AppPrincipal principal) {
        return principal.isMember() ? upsertMember(principal.memberId()) : upsertDevice(principal.deviceKey());
    }

    @Query(value = """
            select (select count(*) from watch_item wi join watch_group g on g.id = wi.group_id where g.principal_id = :p)
                 + (select count(*) from principal_theme where principal_id = :p)
                 + (select count(*) from alert_etf where principal_id = :p)
            """, nativeQuery = true)
    long countOwnedData(@Param("p") long principalId);

    /** 회원 쪽 관심 그룹을 비운다. 이전 전에 호출(그룹 key 유니크 충돌 방지). 별도 문장인 이유는 유니크 검사가 즉시라서다. */
    @Modifying
    @Query(value = """
            with gone as (delete from watch_group where principal_id = :p returning id)
            delete from watch_item where group_id in (select id from gone)
            """, nativeQuery = true)
    void clearWatchData(@Param("p") long principalId);

    /** 디바이스 principal 의 소유 행을 회원 principal 로 옮긴다. */
    @Modifying
    @Query(value = """
            with themes as (update principal_theme set principal_id = :to where principal_id = :from),
                 alerts as (update alert_etf set principal_id = :to where principal_id = :from)
            update watch_group set principal_id = :to where principal_id = :from
            """, nativeQuery = true)
    void transferOwnership(@Param("from") long fromPrincipalId, @Param("to") long toPrincipalId);
}
