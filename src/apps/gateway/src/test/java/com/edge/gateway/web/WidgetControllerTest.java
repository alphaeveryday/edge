package com.edge.gateway.web;

import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import com.edge.gateway.client.WidgetApiClient;
import com.edge.gateway.tenant.StubTenantResolver;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * gateway 라우팅의 "의도"를 검증한다(Rule 9): gateway는 데이터를 만들지 않고 widget-api로 포워딩해
 * 응답을 그대로 돌려준다. 포워딩이 실패하면 위젯 error 상태로 폴백한다.
 *
 * <p>widget-api 호출은 fake {@link WidgetApiClient}로 대체(실 HTTP 불필요).
 */
class WidgetControllerTest {

    private MockMvc mvcWith(WidgetApiClient client) {
        WidgetController controller = new WidgetController(new StubTenantResolver(), client);
        return MockMvcBuilders.standaloneSetup(controller).build();
    }

    private static final String REQ = "{\"embedKey\":\"pub_demo_1234\",\"symbol\":\"005930\"}";

    @Test
    void widget_api_응답을_그대로_포워딩한다() throws Exception {
        // widget-api가 만든 위젯 표준 응답을 흉내
        WidgetApiClient fake = req ->
                "{\"status\":\"success\",\"symbol\":\"" + req.symbol() + "\",\"cards\":[{\"title\":null,\"description\":\"x\"}]}";
        mvcWith(fake).perform(post("/api/v1/widget/analysis")
                        .contentType(MediaType.APPLICATION_JSON).content(REQ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("success"))
                .andExpect(jsonPath("$.symbol").value("005930"))
                .andExpect(jsonPath("$.cards.length()").value(1));
    }

    @Test
    void 포워딩_실패시_위젯_error상태로_폴백() throws Exception {
        WidgetApiClient down = req -> {
            throw new RuntimeException("widget-api unreachable");
        };
        mvcWith(down).perform(post("/api/v1/widget/analysis")
                        .contentType(MediaType.APPLICATION_JSON).content(REQ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("error"))
                .andExpect(jsonPath("$.symbol").value("005930"))
                .andExpect(jsonPath("$.message").isNotEmpty());
    }
}
