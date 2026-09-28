package com.edge.app.story.controller;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.story.dto.StoryResponse;
import com.edge.app.story.service.StoryService;
import com.edge.common.apipayload.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/v1/stories")
@RequiredArgsConstructor
public class StoryController {
    private final StoryService storyService;

    @GetMapping
    public ApiResponse<List<StoryResponse>> storyQueue(AppPrincipal principal) {
        return ApiResponse.onSuccess(storyService.queue(principal));
    }
}
