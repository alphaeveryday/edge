package com.edge.app.sync;

import com.edge.app.ContainerTests;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;

import static com.edge.app.ApiCalls.call;
import static org.junit.jupiter.api.Assertions.assertEquals;

/** 파이프라인 RDS 불통 시에도 동기화 외 API 의 정상 응답 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "app.pipeline.url=jdbc:postgresql://localhost:1/edge", "app.pipeline.username=x", "app.pipeline.password=x",
        "app.sync.initial-delay=PT1H"})
class PipelineDownTests extends ContainerTests {
    @LocalServerPort
    int port;

    @Test
    void appStartsAndServesWhenPipelineUnreachable() {
        assertEquals(200, call(port, "GET", "/api/v1/themes", null).getStatusCode().value());
    }
}
