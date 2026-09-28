package com.edge.app.issue.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.common.cursor.Cursor;
import com.edge.app.common.cursor.PageResponse;
import com.edge.app.issue.dto.IssueDetailResponse;
import com.edge.app.issue.dto.IssueRowResponse;
import org.springframework.stereotype.Service;

import java.util.List;

/** 스텁. */
@Service
public class IssueService {
    public PageResponse<IssueRowResponse> list(AppPrincipal principal, String tab, Cursor cursor, int size) {
        return new PageResponse<>(List.of(IssueExamples.row()), null);
    }

    public IssueDetailResponse get(String id) {
        return IssueExamples.detail(id);
    }
}
