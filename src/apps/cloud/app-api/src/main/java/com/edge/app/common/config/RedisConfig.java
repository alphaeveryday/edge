package com.edge.app.common.config;

import io.lettuce.core.ClientOptions;
import io.lettuce.core.ReadFrom;
import io.lettuce.core.SocketOptions;
import io.lettuce.core.TimeoutOptions;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.data.redis.autoconfigure.LettuceClientConfigurationBuilderCustomizer;
import org.springframework.boot.data.redis.autoconfigure.LettuceClientOptionsBuilderCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Duration;

@Configuration
public class RedisConfig {
    @Bean
    LettuceClientConfigurationBuilderCustomizer redisConfiguration(
            @Value("${spring.data.redis.timeout}") Duration timeout,
            @Value("${vote.redis.read-from}") String readFrom) {
        return builder -> builder.commandTimeout(timeout).readFrom(ReadFrom.valueOf(readFrom));
    }

    // Boot 가 만든 빌더 위의 설정 추가
    // Cluster 에서 topology refresh 설정을 담은 Boot 빌더
    // 새 ClientOptions 의 clientOptions() 주입 시 refresh 설정 전체 유실
    @Bean
    LettuceClientOptionsBuilderCustomizer redisOptions(
            @Value("${spring.data.redis.timeout}") Duration timeout,
            @Value("${vote.redis.disconnected-behavior}") ClientOptions.DisconnectedBehavior behavior,
            @Value("${vote.redis.timeout-options}") boolean expiry) {
        return builder -> builder.disconnectedBehavior(behavior)
                .socketOptions(SocketOptions.builder().connectTimeout(timeout).build())
                .timeoutOptions(TimeoutOptions.builder().timeoutCommands(expiry).fixedTimeout(timeout).build());
    }
}
