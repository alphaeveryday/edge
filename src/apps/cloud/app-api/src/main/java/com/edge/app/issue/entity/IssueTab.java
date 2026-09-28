package com.edge.app.issue.entity;

// 쿼리 파라미터 tab 의 계약값(mine | all). 필수, 기본값 없음.
public enum IssueTab {
    MINE, ALL;

    public static IssueTab of(String value) {
        for (IssueTab tab : values()) {
            if (tab.name().toLowerCase().equals(value)) {
                return tab;
            }
        }
        throw new IllegalArgumentException("unknown issue tab: " + value);
    }
}
