package com.edge.app.member.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

/** 회원과 게스트 디바이스의 공통 소유자. 관심·알림·온보딩 행의 유일한 소유 키 */
@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Principal {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(length = 10, nullable = false)
    private String kind;

    @Column(name = "member_id")
    private Long memberId;

    @Column(name = "device_id")
    private Long deviceId;
}
