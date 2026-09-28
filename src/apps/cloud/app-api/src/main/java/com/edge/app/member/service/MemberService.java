package com.edge.app.member.service;

import com.edge.app.member.dto.MeResponse;
import com.edge.app.member.dto.MeUpdateRequest;
import com.edge.app.member.entity.Member;
import com.edge.app.member.repository.DeviceRepository;
import com.edge.app.member.repository.MemberRepository;
import com.edge.app.member.repository.RefreshTokenRepository;
import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;

@Service
@RequiredArgsConstructor
public class MemberService {
    private final MemberRepository memberRepository;
    private final RefreshTokenRepository refreshTokenRepository;
    private final DeviceRepository deviceRepository;

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

    // 액세스 토큰은 만료까지 살아 있으므로 탈퇴와 함께 리프레시를 전부 폐기하고 디바이스 연결을 푼다.
    @Transactional
    public void deleteAccount(long memberId) {
        Instant now = Instant.now();
        active(memberId).withdraw(now);
        refreshTokenRepository.revokeAll(memberId, now);
        deviceRepository.unlinkAll(memberId);
    }

    @Transactional
    public MeResponse acceptDisclaimer(long memberId) {
        Member member = active(memberId);
        member.acceptDisclaimer(Instant.now());
        return MeResponse.from(member);
    }

    // 토큰은 유효하지만 회원이 탈퇴한 경우. 앱이 토큰을 지우도록 COMMON401.
    private Member active(long memberId) {
        return memberRepository.findByIdAndDeletedAtIsNull(memberId)
                .orElseThrow(() -> new GeneralException(ErrorStatus._UNAUTHORIZED));
    }

    private static String withAt(String handle) {
        return handle.startsWith("@") ? handle : "@" + handle;
    }
}
