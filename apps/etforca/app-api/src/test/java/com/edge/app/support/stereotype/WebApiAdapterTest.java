package com.edge.app.support.stereotype;

import com.edge.app.TestContainersConfiguration;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;

import java.lang.annotation.*;

// 커밋 뒤 리스너와 Redis 상태를 그대로 보기 위한 @Transactional 제외
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@Documented
@SpringBootTest(properties = "app.jwt.secret=test-jwt-secret-for-tests-only-32bytes")
@AutoConfigureMockMvc
@Import(TestContainersConfiguration.class)
public @interface WebApiAdapterTest {
}
