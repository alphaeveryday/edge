package com.edge.app.home.controller;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.home.dto.HomeBriefResponse;
import com.edge.app.home.service.HomeService;
import com.edge.common.apipayload.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/home")
@RequiredArgsConstructor
public class HomeController {
    private final HomeService homeService;

    @GetMapping("/brief")
    public ApiResponse<HomeBriefResponse> homeBrief(AppPrincipal principal, @RequestParam(required = false) String group) {
        return ApiResponse.onSuccess(homeService.brief(principal, group));
    }
}
