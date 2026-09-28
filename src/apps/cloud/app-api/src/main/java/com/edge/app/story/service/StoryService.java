package com.edge.app.story.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.story.dto.StoryResponse;
import org.springframework.stereotype.Service;

import java.util.List;

/** 스토리 뷰어는 폐기된 화면(2026-09-28 확인). 생산자 없음, 빈 큐. 라우트·계약·etf_story 정리는 후속. */
@Service
public class StoryService {
    public List<StoryResponse> queue(AppPrincipal principal) {
        return List.of();
    }
}
