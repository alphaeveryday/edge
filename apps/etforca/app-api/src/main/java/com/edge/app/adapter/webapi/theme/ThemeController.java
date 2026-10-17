package com.edge.app.adapter.webapi.theme;

import com.edge.app.application.theme.ThemeService;
import com.edge.app.application.theme.provided.ThemeResponse;
import com.edge.common.apipayload.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/v1/themes")
@RequiredArgsConstructor
public class ThemeController {
    private final ThemeService themeService;

    @GetMapping
    public ApiResponse<List<ThemeResponse>> themeList() {
        return ApiResponse.onSuccess(themeService.list());
    }
}
