package com.edge.widget.web;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 공통 봉투 매핑의 "의도"를 검증한다(Rule 9): 성공은 isSuccess=true + result 에,
 * 도메인 실패는 HTTP status + isSuccess=false + code 로 나가야 한다 — 이 계약이 깨지면
 * 클라이언트 파싱이 깨진다.
 *
 * <p>Boot 테스트 슬라이스(@WebMvcTest) 대신 spring-test 의 standaloneSetup 으로
 * 컨트롤러 + advice 만 조립한다(프레임워크 버전 이동에 견고, 컨텍스트 부팅 불필요).
 */
class GlobalExceptionHandlerTest {

    private MockMvc mvc;

    @BeforeEach
    void setUp() {
        mvc = MockMvcBuilders.standaloneSetup(new ExampleController())
                .setControllerAdvice(new GlobalExceptionHandler())
                .build();
    }

    @Test
    void 성공은_result에_담기고_isSuccess는_true() throws Exception {
        mvc.perform(get("/api/example").param("name", "edge"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.isSuccess").value(true))
                .andExpect(jsonPath("$.code").value("COMMON200"))
                .andExpect(jsonPath("$.result").value("hello, edge"));
    }

    @Test
    void 도메인예외는_status와_isSuccess_false_code로_매핑된다() throws Exception {
        mvc.perform(get("/api/example/boom"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.isSuccess").value(false))
                .andExpect(jsonPath("$.code").value("WIDGET4001"))
                .andExpect(jsonPath("$.message").value("위젯을 찾을 수 없습니다."));
    }
}
