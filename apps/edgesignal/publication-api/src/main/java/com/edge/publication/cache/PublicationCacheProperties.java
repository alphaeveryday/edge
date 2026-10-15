package com.edge.publication.cache;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

/**
 * 다중 인스턴스 캐시 로컬 실험(LOCAL-4/5) 설정. 기본값은 현행 동작과 동일해야 한다 —
 * 실험값은 부하 프로필 compose 의 env 로만 덮는다.
 *
 * @param mode none|caffeine|redis|two-level (미지정=caffeine)
 * @param l2Ttl L2(Redis) TTL — L1(publication.serve-cache-ttl)보다 길어야 한다
 * @param l2Jitter L2 TTL 에 더할 난수 상한 — 동시 만족 만료를 흩는 실험 변수(기본 0)
 */
@ConfigurationProperties(prefix = "publication.cache")
public record PublicationCacheProperties(String mode, Duration l2Ttl, Duration l2Jitter) {
}
