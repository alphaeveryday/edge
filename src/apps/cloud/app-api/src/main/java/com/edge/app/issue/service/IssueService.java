package com.edge.app.issue.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.common.cursor.Cursor;
import com.edge.app.common.cursor.PageResponse;
import com.edge.app.etf.entity.Dir;
import com.edge.app.etf.entity.Signal;
import com.edge.app.issue.dto.IssueDetailResponse;
import com.edge.app.issue.dto.IssueRowResponse;
import org.springframework.stereotype.Service;

import java.util.List;

/** 스텁. */
@Service
public class IssueService {
    public PageResponse<IssueRowResponse> list(AppPrincipal principal, String tab, Cursor cursor, int size) {
        return new PageResponse<>(List.of(new IssueRowResponse("id", 1, 0, "title", "kw",
                new IssueRowResponse.Etf("000000", "name", "theme", "sub"))), null);
    }

    public IssueDetailResponse get(String id) {
        return new IssueDetailResponse(id, "title", "body", List.of("point"),
                List.of(new IssueDetailResponse.Source("title", "pub", "https://example.com")),
                new IssueDetailResponse.Effect("theme", Dir.NEUTRAL, "body"),
                List.of(new IssueDetailResponse.Affected("000000", "name", "theme", 0, 0, Signal.NEUTRAL, false, "sub",
                        Signal.NEUTRAL)));
    }
}
