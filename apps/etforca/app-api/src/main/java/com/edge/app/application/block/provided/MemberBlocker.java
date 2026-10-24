package com.edge.app.application.block.provided;

/**
 * 회원 차단
 */
public interface MemberBlocker {
    void block(long memberId, String handle);
}
