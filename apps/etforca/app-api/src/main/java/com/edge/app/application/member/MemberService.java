package com.edge.app.application.member;

import com.edge.app.application.member.provided.MeResponse;
import com.edge.app.application.member.provided.MeUpdateRequest;
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
public class MemberService {
    private final MemberRepository memberRepository;
    private final RefreshTokenRepository refreshTokenRepository;
    private final DeviceRepository deviceRepository;
    private final PrincipalRepository principalRepository;
    private final ApplicationEventPublisher eventPublisher;

    @Transactional(readOnly = true)
    public MeResponse me(long memberId) {
        return MeResponse.from(active(memberId));
    }

    @Transactional
    public MeResponse update(long memberId, MeUpdateRequest request) {
        Member member = active(memberId);
        String handle = request.handle() == null ? null : withAt(request.handle().trim());
        if (handle != null && !handle.equals(member.getHandle()) && memberRepository.existsByHandle(handle)) {
            throw new GeneralException(ErrorStatus._BAD_REQUEST);
        }
        member.update(request.nick() == null ? null : request.nick().trim(), handle);
        return MeResponse.from(member);
    }

    // 탈퇴 시 리프레시 전부 폐기
    // 탈퇴 시 디바이스 연결 해제
    // 탈퇴 시 글·댓글 외 회원 행 삭제
    @Transactional
    public void deleteAccount(long memberId) {
        Instant now = Instant.now();
        active(memberId).withdraw(now);
        refreshTokenRepository.revokeAll(memberId, now);
        deviceRepository.unlinkAll(memberId);
        principalRepository.findByMemberId(memberId).ifPresent(p -> principalRepository.deleteOwnedData(p.getId()));
        eventPublisher.publishEvent(new MemberWithdrawn(memberId));
    }

    @Transactional
    public MeResponse acceptDisclaimer(long memberId) {
        Member member = active(memberId);
        member.acceptDisclaimer(Instant.now());
        return MeResponse.from(member);
    }

    // 유효 토큰을 가진 탈퇴 회원의 COMMON401 응답
    private Member active(long memberId) {
        return memberRepository.findByIdAndDeletedAtIsNull(memberId)
                .orElseThrow(() -> new GeneralException(ErrorStatus._UNAUTHORIZED));
    }

    private static String withAt(String handle) {
        return handle.startsWith("@") ? handle : "@" + handle;
    }
}
