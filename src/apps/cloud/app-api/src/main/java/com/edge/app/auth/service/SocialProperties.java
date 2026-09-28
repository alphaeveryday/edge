package com.edge.app.auth.service;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** 소셜 idToken 의 audience(앱 client id). 비어 있으면 그 provider 는 검증 실패. */
@ConfigurationProperties("app.social")
public record SocialProperties(String appleClientId, String googleClientId) {
}
