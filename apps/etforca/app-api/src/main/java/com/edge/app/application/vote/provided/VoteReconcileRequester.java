package com.edge.app.application.vote.provided;

/**
 * 집계 재조정 요청
 * - 같은 인스턴스에서 진행 중이면 병합과 false 반환
 * - true는 비동기 요청 접수이며 다른 인스턴스의 잠금 보유 시 실제 작업은 생략
 */
public interface VoteReconcileRequester {
    boolean request();
}
