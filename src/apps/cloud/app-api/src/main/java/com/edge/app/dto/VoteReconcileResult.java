package com.edge.app.dto;

public record VoteReconcileResult(long buyDelta, long holdDelta, long sellDelta) {

    public long missing() {
        return Math.max(0, buyDelta) + Math.max(0, holdDelta) + Math.max(0, sellDelta);
    }

    public long excess() {
        return Math.max(0, -buyDelta) + Math.max(0, -holdDelta) + Math.max(0, -sellDelta);
    }
}
