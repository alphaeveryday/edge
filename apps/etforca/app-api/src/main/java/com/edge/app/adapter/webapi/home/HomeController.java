package com.edge.app.adapter.webapi.home;

import com.edge.app.application.home.provided.HomeBriefResponse;
import com.edge.app.application.home.provided.HomeFinder;
import com.edge.app.application.home.provided.MarketIndexResponse;
import com.edge.app.application.member.provided.AppPrincipal;
import com.edge.common.apipayload.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/v1/home")
@RequiredArgsConstructor
public class HomeController {
    private final HomeFinder homeFinder;

    @GetMapping("/brief")
    public ApiResponse<HomeBriefResponse> homeBrief(AppPrincipal principal, @RequestParam(required = false) String group) {
        return ApiResponse.onSuccess(homeFinder.brief(principal, group));
    }

    @GetMapping("/indices")
    public ApiResponse<List<MarketIndexResponse>> homeIndices() {
        return ApiResponse.onSuccess(homeFinder.indices());
    }
}
