package com.edge.app.controller;

import com.edge.app.dto.VoteCountResponse;
import com.edge.app.dto.VoteRequest;
import com.edge.app.service.VoteService;
import com.edge.common.apipayload.ApiResponse;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Positive;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequiredArgsConstructor
public class VoteController {
    private final VoteService voteService;

    @PostMapping("/api/v1/forecasts/{forecastId}/votes")
    public ApiResponse<Void> vote(@PathVariable Long forecastId,
            @RequestHeader("X-User-Id") @Positive Long userId,
            @RequestBody @Valid VoteRequest request) {
        voteService.vote(forecastId, userId, request.choice());
        return ApiResponse.onSuccess(null);
    }

    @GetMapping("/api/v1/forecasts/{forecastId}/votes/count")
    public ApiResponse<VoteCountResponse> counts(@PathVariable Long forecastId) {
        return ApiResponse.onSuccess(voteService.counts(forecastId));
    }
}
