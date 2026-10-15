package com.edge.app;

import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

import java.security.MessageDigest;
import java.sql.DriverManager;
import java.util.HexFormat;

public abstract class ContainerTests {
    @ServiceConnection
    public static final PostgreSQLContainer POSTGRES = new PostgreSQLContainer(DockerImageName.parse("postgres:16"));
    @ServiceConnection(name = "redis")
    public static final GenericContainer<?> REDIS =
            new GenericContainer<>(DockerImageName.parse("redis:7-alpine")).withExposedPorts(6379);
    static {
        POSTGRES.start();
        REDIS.start();
    }

    /** DB 에 심어 가입 요청에 싣는 가입 테스트용 인증 코드 */
    public static final String SIGNUP_CODE = "000000";

    public static void signupCode(String email) {
        try (var conn = DriverManager.getConnection(POSTGRES.getJdbcUrl(), POSTGRES.getUsername(), POSTGRES.getPassword());
                var st = conn.prepareStatement("insert into signup_code(email, code_hash, expires_at) values (?, ?, now() + interval '10 minutes') "
                        + "on conflict (email) do update set code_hash = excluded.code_hash, attempts = 0, expires_at = excluded.expires_at")) {
            st.setString(1, email);
            st.setString(2, HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(SIGNUP_CODE.getBytes())));
            st.executeUpdate();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    @DynamicPropertySource
    static void jwtSecret(DynamicPropertyRegistry registry) {
        registry.add("app.jwt.secret", () -> "test-jwt-secret-for-tests-only-32bytes");
    }
}
