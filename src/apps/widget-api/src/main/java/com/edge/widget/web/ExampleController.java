package com.edge.widget.web;

import com.edge.common.apipayload.ApiResponse;
import com.edge.common.exception.GeneralException;
import com.edge.widget.error.WidgetErrorStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * apipayload 공통 봉투({@code ApiResponse})·예외 베이스({@code GeneralException}) 사용 예시 — 데모용.
 * 실제 위젯 엔드포인트가 생기면 이 컨트롤러는 교체/삭제한다.
 */
@RestController
public class ExampleController {

    /** 성공 경로: {@code ApiResponse.onSuccess(result)} (SuccessStatus.OK 기반). */
    @GetMapping("/api/example")
    public ApiResponse<String> example(@RequestParam(defaultValue = "world") String name) {
        return ApiResponse.onSuccess("hello, " + name);
    }

    /** 실패 경로: GeneralException(도메인 코드)을 던지면 GlobalExceptionHandler 가 봉투로 매핑. */
    @GetMapping("/api/example/boom")
    public ApiResponse<Void> boom() {
        throw new GeneralException(WidgetErrorStatus.WIDGET_NOT_FOUND);
    }
}
