package com.edge.app.config;

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

    // Boot 가 만든 빌더(Cluster 면 topology refresh 를 담은 ClusterClientOptions.Builder) 위에 얹는다.
    // ClientOptions 를 새로 만들어 clientOptions() 로 넣으면 refresh 설정이 통째로 버려진다(실측).
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
