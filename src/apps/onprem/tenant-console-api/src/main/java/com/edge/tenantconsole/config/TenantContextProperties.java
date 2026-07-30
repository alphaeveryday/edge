package com.edge.tenantconsole.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 콘솔 헤더·사이드바에 표시되는 테넌트 컨텍스트(ALPHA-500) — 온프렘 박스는 테넌트
 * 1:1 이라 배포 설정이 소스다(스키마에 tenant 테이블 없음). 실 테넌트 신원(인증서
 * 바인딩)은 ALPHA-447 소관이며 이 표시값과 별개다.
 */
@ConfigurationProperties(prefix = "console.tenant")
public record TenantContextProperties(String name, String domain, String mark) {
}
