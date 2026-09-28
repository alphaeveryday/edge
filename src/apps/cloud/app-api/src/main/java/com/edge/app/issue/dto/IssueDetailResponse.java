package com.edge.app.issue.dto;

import com.edge.app.etf.entity.Dir;
import com.edge.app.etf.entity.Signal;

import java.util.List;

public record IssueDetailResponse(String id, String title, String body, List<String> points, List<Source> sources,
        Effect effect, List<Affected> affected) {
    public record Source(String title, String pub, String url) {
    }

    public record Effect(String theme, Dir dir, String body) {
    }

    // 계약 EtfSummary + prev
    public record Affected(String code, String name, String theme, double price, double changePct, Signal signal,
            boolean hot, String sub, Signal prev) {
    }
}
