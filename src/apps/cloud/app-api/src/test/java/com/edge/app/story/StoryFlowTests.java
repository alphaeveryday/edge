package com.edge.app.story;

import com.edge.app.ContainerTests;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;

import java.util.List;

import static com.edge.app.ApiCalls.call;
import static com.edge.app.ApiCalls.result;
import static org.junit.jupiter.api.Assertions.assertEquals;

/** 스토리 뷰어는 폐기된 화면이라 큐가 비어 있어야 홈에서 ETF 행이 눌리지 않는다. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class StoryFlowTests extends ContainerTests {
    @LocalServerPort
    int port;

    @Test
    void queueIsEmpty() {
        assertEquals(List.of(), result(call(port, "GET", "/api/v1/stories", null, "X-Device-Id", "s1")));
    }
}
