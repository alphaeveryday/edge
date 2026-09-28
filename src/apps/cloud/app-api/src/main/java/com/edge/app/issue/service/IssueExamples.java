package com.edge.app.issue.service;

import com.edge.app.etf.entity.Dir;
import com.edge.app.etf.entity.Signal;
import com.edge.app.issue.dto.IssueDetailResponse;
import com.edge.app.issue.dto.IssueRowResponse;

import java.util.List;

/** 스텁 고정값. 실구현 때 삭제. */
final class IssueExamples {
    private IssueExamples() {
    }

    static IssueRowResponse row() {
        return new IssueRowResponse("id", 1, 0, "title", "kw", new IssueRowResponse.Etf("000000", "name", "theme", "sub"));
    }

    static IssueDetailResponse detail(String id) {
        return new IssueDetailResponse(id, "title", "body", List.of("point"),
                List.of(new IssueDetailResponse.Source("title", "pub", "https://example.com")),
                new IssueDetailResponse.Effect("theme", Dir.NEUTRAL, "body"),
                List.of(new IssueDetailResponse.Affected("000000", "name", "theme", 0, 0, Signal.NEUTRAL, false, "sub",
                        Signal.NEUTRAL)));
    }
}
