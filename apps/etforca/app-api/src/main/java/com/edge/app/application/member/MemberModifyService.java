package com.edge.app.application.member;

import com.edge.app.application.member.provided.MeUpdateRequest;
import com.edge.app.application.member.provided.MemberFinder;
import com.edge.app.application.member.provided.MemberModifier;
import com.edge.app.application.member.required.DeviceRepository;
import com.edge.app.application.member.required.MemberRepository;
import com.edge.app.application.member.required.PrincipalRepository;
import com.edge.app.application.member.required.RefreshTokenRepository;
import com.edge.app.domain.member.Member;
import com.edge.app.domain.member.MemberWithdrawn;
import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;

@Service
@RequiredArgsConstructor
public class MemberModifyService implements MemberModifier {
    private final MemberFinder memberFinder;
    private final MemberRepository memberRepository;
    private final RefreshTokenRepository refreshTokenRepository;
    private final DeviceRepository deviceRepository;
    private final PrincipalRepository principalRepository;
    private final ApplicationEventPublisher eventPublisher;

    @Override
    @Transactional
    public Member update(long memberId, MeUpdateRequest request) {
        Member member = memberFinder.find(memberId);
        String handle = request.handle() == null ? null : withAt(request.handle().trim());
        if (handle != null && !handle.equals(member.getHandle()) && memberRepository.existsByHandle(handle)) {
            throw new GeneralException(ErrorStatus._BAD_REQUEST);
        }
        member.update(request.nick() == null ? null : request.nick().trim(), handle);
        return member;
    }

    @Override
    @Transactional
    public Member acceptDisclaimer(long memberId) {
        Member member = memberFinder.find(memberId);
        member.acceptDisclaimer(Instant.now());
        return member;
    }

    // 탈퇴 시 리프레시 전부 폐기
    // 탈퇴 시 디바이스 연결 해제
    // 탈퇴 시 글·댓글 외 회원 행 삭제
    @Override
    @Transactional
    public void deleteAccount(long memberId) {
        Instant now = Instant.now();
        memberFinder.find(memberId).withdraw(now);
        refreshTokenRepository.revokeAll(memberId, now);
        deviceRepository.unlinkAll(memberId);
        principalRepository.findByMemberId(memberId).ifPresent(p -> principalRepository.deleteOwnedData(p.getId()));
        eventPublisher.publishEvent(new MemberWithdrawn(memberId));
    }

    private static String withAt(String handle) {
        return handle.startsWith("@") ? handle : "@" + handle;
    }
}
