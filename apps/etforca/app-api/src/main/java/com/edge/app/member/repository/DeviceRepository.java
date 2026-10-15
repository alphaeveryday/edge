package com.edge.app.member.repository;

import com.edge.app.member.entity.Device;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface DeviceRepository extends JpaRepository<Device, Long> {
    Optional<Device> findByDeviceKey(String deviceKey);

    @Modifying
    @Query("update Device d set d.memberId = :member where d.deviceKey = :key")
    void link(@Param("key") String deviceKey, @Param("member") Long memberId);

    @Modifying
    @Query("update Device d set d.memberId = null where d.memberId = :member")
    void unlinkAll(@Param("member") long memberId);

    @Modifying
    @Query("update Device d set d.pushToken = null where d.pushToken = :token and d.deviceKey <> :key")
    void releasePushToken(@Param("key") String deviceKey, @Param("token") String token);

    /** 게스트 등록은 회원 연결 해제, 로그아웃한 기기의 회원 알림 차단 */
    @Modifying
    @Query("update Device d set d.pushToken = :token, d.memberId = :member where d.deviceKey = :key")
    void registerPush(@Param("key") String deviceKey, @Param("token") String token, @Param("member") Long memberId);

    @Query("select d.pushToken from Device d where d.memberId = :member and d.pushToken is not null")
    List<String> memberPushTokens(@Param("member") long memberId);

    @Query("select d.pushToken from Device d where d.id = :id and d.pushToken is not null")
    List<String> devicePushTokens(@Param("id") long deviceId);

    @Transactional
    @Modifying
    @Query("update Device d set d.pushToken = null where d.pushToken in :tokens")
    void dropPushTokens(@Param("tokens") Collection<String> tokens);
}
