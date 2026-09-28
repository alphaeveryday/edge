package com.edge.app.story.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.story.dto.StoryResponse;
import org.springframework.stereotype.Service;

import java.util.List;

/** 스텁. */
@Service
public class StoryService {
    public List<StoryResponse> queue(AppPrincipal principal) {
        return List.of(StoryExamples.story());
    }
}
