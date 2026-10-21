package com.edge.app.batch;

import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Bean;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.test.context.DynamicPropertyRegistrar;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

import java.io.IOException;
import java.nio.charset.StandardCharsets;

// 앱 DB 컨테이너와 그 안 별도 edge 데이터베이스의 파이프라인 원천
@TestConfiguration(proxyBeanMethods = false)
public class BatchTestConfiguration {
    @Bean
    @ServiceConnection
    PostgreSQLContainer postgres() {
        return new PostgreSQLContainer(DockerImageName.parse("postgres:16"));
    }

    @Bean
    public PipelineDatabase pipelineDatabase(PostgreSQLContainer postgres) throws IOException {
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

    public record PipelineDatabase(String url, String username, String password, JdbcTemplate jdbc) {
    }
}
