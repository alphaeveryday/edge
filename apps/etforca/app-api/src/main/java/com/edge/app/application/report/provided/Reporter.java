package com.edge.app.application.report.provided;

/**
 * 글·댓글 신고
 */
public interface Reporter {
    void report(long memberId, ReportRequest request);
}
