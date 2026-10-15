package com.edge.app.notification.service;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import java.util.List;
import java.util.Map;
import java.util.stream.IntStream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withServerError;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

/** 무효 토큰만 정리 대상으로 돌려주고, 다른 오류와 배치 실패는 정리하지 않음 */
class ExpoPushClientTests {
    private final RestClient.Builder builder = RestClient.builder().baseUrl("https://exp.host");
    private final MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
    private final ExpoPushClient client = new ExpoPushClient(builder.build());

    @Test
    void returnsOnlyDeviceNotRegisteredTokens() {
        server.expect(requestTo("https://exp.host/--/api/v2/push/send")).andExpect(method(HttpMethod.POST))
                .andRespond(withSuccess("""
                        {"data":[{"status":"ok","id":"x"},
                          {"status":"error","message":"gone","details":{"error":"DeviceNotRegistered"}},
                          {"status":"error","message":"too big","details":{"error":"MessageTooBig"}}]}
                        """, MediaType.APPLICATION_JSON));

        assertEquals(List.of("t2"), client.send(List.of("t1", "t2", "t3"), "제목", "본문", Map.of()));
        server.verify();
    }

    @Test
    void splitsIntoBatchesOfHundredAndSkipsFailedBatch() {
        List<String> tokens = IntStream.range(0, 101).mapToObj(i -> "t" + i).toList();
        server.expect(requestTo("https://exp.host/--/api/v2/push/send")).andRespond(withServerError());
        server.expect(requestTo("https://exp.host/--/api/v2/push/send")).andRespond(withSuccess(
                "{\"data\":[{\"status\":\"error\",\"details\":{\"error\":\"DeviceNotRegistered\"}}]}", MediaType.APPLICATION_JSON));

        assertEquals(List.of("t100"), client.send(tokens, "제목", "본문", Map.of()));
        server.verify();
    }
}
