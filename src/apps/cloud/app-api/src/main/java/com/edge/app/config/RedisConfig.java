package com.edge.app.config;

import io.lettuce.core.ClientOptions;
import io.lettuce.core.ReadFrom;
import io.lettuce.core.SocketOptions;
import io.lettuce.core.TimeoutOptions;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.data.redis.autoconfigure.LettuceClientConfigurationBuilderCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Duration;

@Configuration
public class RedisConfig {
    @Bean
    LettuceClientConfigurationBuilderCustomizer redisOptions(
            @Value("${spring.data.redis.timeout}") Duration timeout,
            @Value("${vote.redis.disconnected-behavior}") ClientOptions.DisconnectedBehavior behavior,
            @Value("${vote.redis.timeout-options}") boolean expiry,
            @Value("${vote.redis.read-from}") String readFrom) {
        return builder -> builder.commandTimeout(timeout).readFrom(ReadFrom.valueOf(readFrom))
                .clientOptions(ClientOptions.builder().disconnectedBehavior(behavior)
                        .socketOptions(SocketOptions.builder().connectTimeout(timeout).build())
                        .timeoutOptions(TimeoutOptions.builder().timeoutCommands(expiry).fixedTimeout(timeout).build()).build());
    }
}
