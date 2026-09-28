package com.edge.app.story.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.etf.entity.Dir;
import com.edge.app.etf.entity.Signal;
import com.edge.app.etf.service.EtfService;
import com.edge.app.story.dto.StoryResponse;
import org.springframework.stereotype.Service;

import java.util.List;

/** 스텁. */
@Service
public class StoryService {
    public List<StoryResponse> queue(AppPrincipal principal) {
        var ai = new StoryResponse.Ai("sec", "badge", 0, "headline", "noteTitle", List.of("note"),
                List.of(new StoryResponse.NewsRef("issueId", "t", "phase", "kw", Dir.NEUTRAL)));
        var news = new StoryResponse.News("sec", "issueId", "t", "kw", "b");
        var hook = new StoryResponse.Hook("sec", "big", "capPre", "capB", "capPost");
        return List.of(new StoryResponse(EtfService.SUMMARY, "sub", Signal.NEUTRAL, List.of(ai, news, hook)));
    }
}
