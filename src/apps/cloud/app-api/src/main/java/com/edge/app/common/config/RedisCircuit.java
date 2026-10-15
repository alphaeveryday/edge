package com.edge.app.common.config;

import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.lettuce.core.cluster.RedisClusterClient;
import io.lettuce.core.cluster.SlotHash;
import io.lettuce.core.cluster.models.partitions.RedisClusterNode;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.stereotype.Component;

import java.util.Collections;

// 소유 노드의 첫 슬롯을 쓰는 샤드 서킷 이름
// Lettuce 가 refresh 와 MOVED 로 유지하는 Partitions 의 슬롯 표 사용
// 노드만 바뀌는 페일오버에서의 서킷 상태 유지
// 소유자가 바뀌는 리샤딩에서 옮긴 슬롯의 새 샤드 서킷 이동
@Component
public class RedisCircuit {
    private final CircuitBreakerRegistry registry;
    private final RedisClusterClient client;

    public RedisCircuit(CircuitBreakerRegistry registry, RedisConnectionFactory factory,
            @Value("${vote.circuit.scope:shard}") String scope) {
        this.registry = registry;
        this.client = "shard".equals(scope) && factory instanceof LettuceConnectionFactory lettuce
                && lettuce.getNativeClient() instanceof RedisClusterClient cluster ? cluster : null;
    }

    public CircuitBreaker of(String etfCode) {
        if (client == null) {
            return registry.circuitBreaker("redis");
        }
        int slot = SlotHash.getSlot("vote:{" + etfCode + "}:count");
        var partitions = client.getPartitions();
        // getMasterBySlot 이 빈 캐시 배열을 인덱싱해 예외를 내는 빈 Partitions
        // null 가드 앞에서 걸러 내는 전역 서킷 처리
        RedisClusterNode master = partitions.isEmpty() ? null : partitions.getMasterBySlot(slot);
        if (master == null || master.getSlots().isEmpty()) {
            return registry.circuitBreaker("redis");
        }
        return registry.circuitBreaker("redis-shard-" + Collections.min(master.getSlots()),
                registry.circuitBreaker("redis").getCircuitBreakerConfig());
    }
}
