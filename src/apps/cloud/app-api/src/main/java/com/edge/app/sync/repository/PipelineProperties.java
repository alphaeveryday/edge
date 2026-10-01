package com.edge.app.sync.repository;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** 파이프라인 RDS 읽기 전용 접속. url 이 없으면 동기화 미동작 */
@ConfigurationProperties("app.pipeline")
public record PipelineProperties(String url, String username, String password) {
}
