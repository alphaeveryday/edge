package com.edge.app.theme.controller;

import com.edge.app.theme.dto.ThemeDetailResponse;
import com.edge.app.theme.dto.ThemeFeedItemResponse;
import com.edge.app.theme.dto.ThemeResponse;
import com.edge.app.theme.service.ThemeService;
import com.edge.common.apipayload.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
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

    @GetMapping("/feed")
    public ApiResponse<List<ThemeFeedItemResponse>> themeFeed() {
        return ApiResponse.onSuccess(themeService.feed());
    }

    @GetMapping("/{key}")
    public ApiResponse<ThemeDetailResponse> themeDetail(@PathVariable String key) {
        return ApiResponse.onSuccess(themeService.detail(key));
    }
}
