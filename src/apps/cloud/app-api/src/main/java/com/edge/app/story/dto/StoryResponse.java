package com.edge.app.story.dto;

import com.edge.app.etf.dto.EtfSummaryResponse;
import com.edge.app.etf.entity.Dir;
import com.edge.app.etf.entity.Signal;
import com.fasterxml.jackson.annotation.JsonSubTypes;
import com.fasterxml.jackson.annotation.JsonTypeInfo;

import java.util.List;

public record StoryResponse(EtfSummaryResponse etf, String sub, Signal prev, List<Card> cards) {
    // 계약 StoryCard oneOf. kind 가 판별자.
    @JsonTypeInfo(use = JsonTypeInfo.Id.NAME, property = "kind")
    @JsonSubTypes({
            @JsonSubTypes.Type(value = Ai.class, name = "ai"),
            @JsonSubTypes.Type(value = News.class, name = "news"),
            @JsonSubTypes.Type(value = Hook.class, name = "hook")})
    public sealed interface Card permits Ai, News, Hook {
    }

    public record Ai(String sec, String badge, double changePct, String headline, String noteTitle, List<String> notes,
            List<NewsRef> news) implements Card {
    }

    public record NewsRef(String issueId, String t, String phase, String kw, Dir dir) {
    }

    public record News(String sec, String issueId, String t, String kw, String b) implements Card {
    }

    public record Hook(String sec, String big, String capPre, String capB, String capPost) implements Card {
    }
}
