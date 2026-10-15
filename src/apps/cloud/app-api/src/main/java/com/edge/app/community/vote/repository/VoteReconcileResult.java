package com.edge.app.community.vote.repository;

public record VoteReconcileResult(long buyDelta, long waitDelta, long sellDelta) {

    public long missing() {
        return Math.max(0, buyDelta) + Math.max(0, waitDelta) + Math.max(0, sellDelta);
    }

    public long excess() {
        return Math.max(0, -buyDelta) + Math.max(0, -waitDelta) + Math.max(0, -sellDelta);
    }
}
