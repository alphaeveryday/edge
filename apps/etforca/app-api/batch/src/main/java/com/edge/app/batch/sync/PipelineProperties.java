package com.edge.app.batch.sync;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** 파이프라인 RDS 읽기 전용 접속, 없거나 불통이면 기동 실패 */
@ConfigurationProperties("app.pipeline")
public record PipelineProperties(String url, String username, String password) {
}
