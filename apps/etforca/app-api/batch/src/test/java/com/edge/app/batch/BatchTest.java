package com.edge.app.batch;

import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;

import java.lang.annotation.Documented;
import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

// 앱 DB 스키마 소유자인 app-api 마이그레이션 적용과 기동 시 잡 자동 실행 끔
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@Documented
@SpringBootTest(properties = {
        "spring.batch.job.enabled=false",
        "spring.flyway.locations=filesystem:../src/main/resources/db/etf-migration",
        "spring.flyway.table=flyway_etf_schema_history"})
@Import(BatchTestConfiguration.class)
public @interface BatchTest {
}
