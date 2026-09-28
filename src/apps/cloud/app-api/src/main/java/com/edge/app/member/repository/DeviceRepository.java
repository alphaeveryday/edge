package com.edge.app.member.repository;

import com.edge.app.member.entity.Device;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface DeviceRepository extends JpaRepository<Device, Long> {
    Optional<Device> findByDeviceKey(String deviceKey);

    @Modifying
    @Query("update Device d set d.memberId = :member where d.deviceKey = :key")
    void link(@Param("key") String deviceKey, @Param("member") Long memberId);

    @Modifying
    @Query("update Device d set d.memberId = null where d.memberId = :member")
    void unlinkAll(@Param("member") long memberId);
}
