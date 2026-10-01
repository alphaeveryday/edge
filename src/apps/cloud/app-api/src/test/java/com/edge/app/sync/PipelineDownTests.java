package com.edge.app.sync;

import com.edge.app.ContainerTests;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;

import static com.edge.app.ApiCalls.call;
import static org.junit.jupiter.api.Assertions.assertEquals;

/** 파이프라인 RDS 가 불통이어도 앱은 기동해 다른 API 를 계속 응답한다. 동기화만 실패해야 한다 */
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
