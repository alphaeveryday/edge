package com.edge.app.auth.event;

import com.edge.app.auth.service.AppleTokenService;
import com.edge.app.member.event.MemberWithdrawn;
import com.edge.app.member.repository.AppleTokenRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.event.TransactionalEventListener;

/**
 * 탈퇴 회원의 애플 로그인 연결 철회
 * 외부 호출이라 탈퇴 커밋 뒤 실행, 실패해도 탈퇴 유지
 * 커밋 뒤 단계의 삭제 반영을 위한 새 트랜잭션
 */
@Component
@RequiredArgsConstructor
public class AppleWithdrawListener {
    private final AppleTokenService appleTokens;
    private final AppleTokenRepository appleTokenRepository;

    @TransactionalEventListener
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void on(MemberWithdrawn event) {
        appleTokenRepository.findById(event.memberId()).ifPresent(token -> {
            appleTokens.revoke(token.getRefreshToken());
            appleTokenRepository.delete(token);
        });
    }
}
