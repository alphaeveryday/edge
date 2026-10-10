package com.edge.app.auth.service;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 비어 있으면 해당 provider 검증이 실패하는 소셜 idToken audience
 * 애플 팀·키·비공개 키는 탈퇴 시 연결 철회용, 비면 교환·철회 생략
 */
@ConfigurationProperties("app.social")
public record SocialProperties(String appleClientId, String googleClientId, String kakaoClientId,
        String appleTeamId, String appleKeyId, String applePrivateKey) {
}
