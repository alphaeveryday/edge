package com.edge.app.adapter.webapi.explore;

import com.edge.app.application.explore.ExploreService;
import com.edge.app.application.explore.provided.RankRowResponse;
import com.edge.common.apipayload.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/v1/explore")
@RequiredArgsConstructor
public class ExploreController {
    private final ExploreService exploreService;

    @GetMapping("/rank")
    public ApiResponse<List<RankRowResponse>> exploreRank() {
        return ApiResponse.onSuccess(exploreService.rank());
    }
}
