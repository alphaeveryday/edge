package com.edge.app.issue.dto;

public record IssueRowResponse(String id, int rank, int delta, String title, String kw, Etf etf) {
    public record Etf(String code, String name, String theme, String sub) {
    }
}
