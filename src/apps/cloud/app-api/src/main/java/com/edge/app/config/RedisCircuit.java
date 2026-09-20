package com.edge.app.config;

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

// 샤드 서킷 이름은 소유 노드의 첫 슬롯. 슬롯 표는 Lettuce 가 refresh/MOVED 로 유지하는 Partitions 를 그대로 쓴다 —
// 페일오버(노드만 바뀜)엔 상태가 이어지고, 리샤딩(소유자 바뀜)엔 옮긴 슬롯이 새 샤드 서킷으로 따라간다.
@Component
public class RedisCircuit {
    private final CircuitBreakerRegistry registry;
    private final RedisClusterClient client;

    public RedisCircuit(CircuitBreakerRegistry registry, RedisConnectionFactory factory,
            @Value("${vote.circuit.scope:global}") String scope) {
        this.registry = registry;
        this.client = "shard".equals(scope) && factory instanceof LettuceConnectionFactory lettuce
                && lettuce.getNativeClient() instanceof RedisClusterClient cluster ? cluster : null;
    }

    public CircuitBreaker of(Long forecastId) {
        if (client == null) {
            return registry.circuitBreaker("redis");
        }
        int slot = SlotHash.getSlot("vote:{" + forecastId + "}:count");
        RedisClusterNode master = client.getPartitions().getMasterBySlot(slot);
        if (master == null || master.getSlots().isEmpty()) {
            return registry.circuitBreaker("redis");
        }
        return registry.circuitBreaker("redis-shard-" + Collections.min(master.getSlots()),
                registry.circuitBreaker("redis").getCircuitBreakerConfig());
    }
}
