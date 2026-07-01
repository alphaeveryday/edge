package com.edge.widget.web;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import com.edge.widget.analysis.MockAnalysisClient;
import com.edge.widget.widget.WidgetResponseAdapter;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 위젯 표준 응답 생산(모델 A: widget-api)의 "의도"를 검증한다(Rule 9): 프론트는 body의 {@code status}
 * 문자열로 4상태를 분기하므로, 각 상태가 계약대로 생산돼야 한다.
 */
class WidgetInternalControllerTest {

    private MockMvc mvc;

    @BeforeEach
    void setUp() {
        WidgetInternalController controller =
                new WidgetInternalController(new MockAnalysisClient(), new WidgetResponseAdapter());
        mvc = MockMvcBuilders.standaloneSetup(controller).build();
    }

    private org.springframework.test.web.servlet.ResultActions call(String symbol) throws Exception {
        String body = "{\"symbol\":\"" + symbol + "\"}";
        return mvc.perform(post("/internal/widget/analysis")
                .contentType(MediaType.APPLICATION_JSON)
                .content(body));
    }

    @Test
    void 정상심볼은_success로_카드를_담아_반환() throws Exception {
        call("005930")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("success"))
                .andExpect(jsonPath("$.cards.length()").value(1))
                .andExpect(jsonPath("$.fallback.isFallback").value(false));
    }

    @Test
    void EMPTY심볼은_empty상태() throws Exception {
        call("EMPTY")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("empty"))
                .andExpect(jsonPath("$.cards.length()").value(0));
    }

    @Test
    void ERROR심볼은_error상태_message포함() throws Exception {
        call("ERROR")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("error"))
                .andExpect(jsonPath("$.message").isNotEmpty())
                .andExpect(jsonPath("$.cards").doesNotExist());
    }

    @Test
    void FALLBACK심볼은_fallback상태() throws Exception {
        call("FALLBACK")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("fallback"))
                .andExpect(jsonPath("$.fallback.isFallback").value(true));
    }
}
