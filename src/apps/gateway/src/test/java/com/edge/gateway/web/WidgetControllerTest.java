package com.edge.gateway.web;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import com.edge.gateway.analysis.MockAnalysisClient;
import com.edge.gateway.tenant.StubTenantResolver;
import com.edge.gateway.widget.WidgetResponseAdapter;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 위젯 표준 응답 계약의 "의도"를 검증한다(Rule 9): 프론트는 body의 {@code status} 문자열로 4상태를
 * 분기하므로, 각 상태가 계약대로 나가야 프론트 렌더가 깨지지 않는다.
 *
 * <p>Boot 슬라이스(@WebMvcTest) 대신 standaloneSetup으로 컨트롤러+협력자를 직접 조립.
 */
class WidgetControllerTest {

    private MockMvc mvc;

    @BeforeEach
    void setUp() {
        WidgetController controller = new WidgetController(
                new StubTenantResolver(), new MockAnalysisClient(), new WidgetResponseAdapter());
        mvc = MockMvcBuilders.standaloneSetup(controller).build();
    }

    private org.springframework.test.web.servlet.ResultActions call(String symbol) throws Exception {
        String body = "{\"embedKey\":\"pub_demo_1234\",\"symbol\":\"" + symbol + "\"}";
        return mvc.perform(post("/api/v1/widget/analysis")
                .contentType(MediaType.APPLICATION_JSON)
                .content(body));
    }

    @Test
    void 정상심볼은_success로_카드를_담아_반환() throws Exception {
        call("005930")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("success"))
                .andExpect(jsonPath("$.symbol").value("005930"))
                .andExpect(jsonPath("$.cards.length()").value(1))
                .andExpect(jsonPath("$.cards[0].description").isNotEmpty())
                .andExpect(jsonPath("$.fallback.isFallback").value(false));
    }

    @Test
    void EMPTY심볼은_empty상태_summary공백_cards빈배열() throws Exception {
        call("EMPTY")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("empty"))
                .andExpect(jsonPath("$.summary").value(""))
                .andExpect(jsonPath("$.cards.length()").value(0));
    }

    @Test
    void ERROR심볼은_error상태_message포함_cards없음() throws Exception {
        call("ERROR")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("error"))
                .andExpect(jsonPath("$.message").isNotEmpty())
                .andExpect(jsonPath("$.cards").doesNotExist());
    }

    @Test
    void FALLBACK심볼은_fallback상태_블록채움() throws Exception {
        call("FALLBACK")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("fallback"))
                .andExpect(jsonPath("$.fallback.isFallback").value(true))
                .andExpect(jsonPath("$.fallback.reason").isNotEmpty());
    }
}
