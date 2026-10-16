package com.edge.app.sync;

import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.test.context.DynamicPropertyRegistrar;
import org.testcontainers.postgresql.PostgreSQLContainer;

import java.io.IOException;
import java.nio.charset.StandardCharsets;

// 앱 DB 컨테이너 안 별도 edge 데이터베이스의 파이프라인 원천
@TestConfiguration(proxyBeanMethods = false)
class PipelineTestConfiguration {
    @Bean
    PipelineDatabase pipelineDatabase(PostgreSQLContainer postgres) throws IOException {
        new JdbcTemplate(new DriverManagerDataSource(postgres.getJdbcUrl(), postgres.getUsername(), postgres.getPassword()))
                .execute("create database edge");
        String url = postgres.getJdbcUrl().replaceFirst("/[^/?]+(\\?|$)", "/edge$1");
        JdbcTemplate jdbc = new JdbcTemplate(new DriverManagerDataSource(url, postgres.getUsername(), postgres.getPassword()));
        jdbc.execute(new ClassPathResource("pipeline-schema.sql").getContentAsString(StandardCharsets.UTF_8));
        return new PipelineDatabase(url, postgres.getUsername(), postgres.getPassword(), jdbc);
    }

    @Bean
    DynamicPropertyRegistrar pipelineProperties(PipelineDatabase pipeline) {
        return registry -> {
            registry.add("app.pipeline.url", pipeline::url);
            registry.add("app.pipeline.username", pipeline::username);
            registry.add("app.pipeline.password", pipeline::password);
        };
    }

    record PipelineDatabase(String url, String username, String password, JdbcTemplate jdbc) {
    }
}
