package com.edge.widget.web;

import com.edge.common.response.ApiResponse;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * jvm-common 공통 봉투({@code ApiResponse}) 사용 예시 — 데모용.
 * 실제 위젯 엔드포인트가 생기면 이 컨트롤러는 교체/삭제한다.
 */
@RestController
public class ExampleController {

    /** 성공 경로: {@code ApiResponse.ok(data)}. */
    @GetMapping("/api/example")
    public ApiResponse<String> example(@RequestParam(defaultValue = "world") String name) {
        return ApiResponse.ok("hello, " + name);
    }

    /** 실패 경로: 도메인 예외를 던지면 GlobalExceptionHandler 가 봉투로 매핑. */
    @GetMapping("/api/example/boom")
    public ApiResponse<Void> boom() {
        throw new ApiException(HttpStatus.NOT_FOUND, "WIDGET_NOT_FOUND", "위젯을 찾을 수 없습니다.");
    }
}
