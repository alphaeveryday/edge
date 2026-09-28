package com.edge.app.story.service;

import com.edge.app.etf.entity.Dir;
import com.edge.app.etf.entity.Signal;
import com.edge.app.etf.service.EtfExamples;
import com.edge.app.story.dto.StoryResponse;

import java.util.List;

/** 스텁 고정값. 실구현 때 삭제. */
final class StoryExamples {
    private StoryExamples() {
    }

    static StoryResponse story() {
        var ai = new StoryResponse.Ai("sec", "badge", 0, "headline", "noteTitle", List.of("note"),
                List.of(new StoryResponse.NewsRef("issueId", "t", "phase", "kw", Dir.NEUTRAL)));
        var news = new StoryResponse.News("sec", "issueId", "t", "kw", "b");
        var hook = new StoryResponse.Hook("sec", "big", "capPre", "capB", "capPost");
        return new StoryResponse(EtfExamples.summary(), "sub", Signal.NEUTRAL, List.of(ai, news, hook));
    }
}
