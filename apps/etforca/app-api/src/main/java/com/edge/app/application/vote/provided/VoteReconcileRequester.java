package com.edge.app.application.vote.provided;

/**
 * 집계 재조정 요청
 * - 진행 중이면 병합과 false 반환
 */
public interface VoteReconcileRequester {
    boolean request();
}
