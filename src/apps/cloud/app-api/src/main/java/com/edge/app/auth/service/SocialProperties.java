package com.edge.app.auth.service;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** 소셜 idToken audience. 비면 해당 provider 검증 실패 */
@ConfigurationProperties("app.social")
public record SocialProperties(String appleClientId, String googleClientId) {
}
