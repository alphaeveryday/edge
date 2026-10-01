package com.edge.app.member.repository;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.member.entity.Principal;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

/**
 * 요청 principal 의 id 해소. 처음 보는 디바이스는 device·principal 행 생성.
 * 다른 도메인의 유일한 진입점. 로그인 매핑도 여기 소유.
 * 소유자 이전은 principal 규칙이라 관심·온보딩·알림 테이블 직접 UPDATE.
 */
public interface PrincipalRepository extends JpaRepository<Principal, Long> {
    Optional<Principal> findByMemberId(long memberId);

    Optional<Principal> findByDeviceId(long deviceId);

    // device·principal 단문 upsert. no-op update 는 기존 id RETURNING 용도
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

    /** 회원 관심 그룹 비우기. 이전 전 호출로 그룹 key 유니크 충돌 방지 */
    @Modifying
    @Query(value = """
            with gone as (delete from watch_group where principal_id = :p returning id)
            delete from watch_item where group_id in (select id from gone)
            """, nativeQuery = true)
    void clearWatchData(@Param("p") long principalId);

    /** 탈퇴 회원 principal 소유 행 삭제 */
    @Modifying
    @Query(value = """
            with groups as (delete from watch_group where principal_id = :p returning id),
                 items as (delete from watch_item where group_id in (select id from groups)),
                 themes as (delete from principal_theme where principal_id = :p),
                 alerts as (delete from alert_etf where principal_id = :p)
            delete from notification where principal_id = :p
            """, nativeQuery = true)
    void deleteOwnedData(@Param("p") long principalId);

    /** 디바이스 principal 소유 행의 회원 이전 */
    @Modifying
    @Query(value = """
            with themes as (update principal_theme set principal_id = :to where principal_id = :from),
                 alerts as (update alert_etf set principal_id = :to where principal_id = :from)
            update watch_group set principal_id = :to where principal_id = :from
            """, nativeQuery = true)
    void transferOwnership(@Param("from") long fromPrincipalId, @Param("to") long toPrincipalId);
}
