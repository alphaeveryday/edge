package com.edge.app.auth.service;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** 비어 있으면 해당 provider 검증이 실패하는 소셜 idToken audience */
@ConfigurationProperties("app.social")
public record SocialProperties(String appleClientId, String googleClientId) {
}
