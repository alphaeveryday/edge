package com.edge.app.auth.service;

import com.edge.app.auth.dto.AuthResponse;
import com.edge.app.auth.dto.LoginRequest;
import com.edge.app.auth.dto.PasswordResetRequest;
import com.edge.app.auth.dto.RefreshRequest;
import com.edge.app.auth.dto.SignupRequest;
import com.edge.app.auth.dto.SocialLoginRequest;
import com.edge.app.common.AppErrorStatus;
import com.edge.app.common.auth.AccessTokens;
import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.member.dto.MeResponse;
import com.edge.app.member.entity.Member;
import com.edge.app.member.entity.Provider;
import com.edge.app.member.entity.RefreshToken;
import com.edge.app.member.repository.DeviceRepository;
import com.edge.app.member.repository.MemberRepository;
import com.edge.app.member.repository.PrincipalRepository;
import com.edge.app.member.repository.RefreshTokenRepository;
import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Duration;
import java.time.Instant;
import java.util.Base64;
import java.util.HexFormat;

/**
 * 가입·로그인은 member 행을 여기서 만든다(가입은 auth 의 쓰기). 그 뒤 회원 상태 변경은 MemberService.
 * 리프레시 토큰은 불투명 난수, DB 에는 sha-256 만. 재발급마다 회전한다.
 */
@Service
@RequiredArgsConstructor
public class AuthService {
    private static final Duration REFRESH_TTL = Duration.ofDays(30);
    private static final String HANDLE_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";
    private static final SecureRandom RANDOM = new SecureRandom();

    private final AccessTokens tokens;
    private final IdTokenVerifier idTokenVerifier;
    private final MemberRepository memberRepository;
    private final PrincipalRepository principalRepository;
    private final DeviceRepository deviceRepository;
    private final RefreshTokenRepository refreshTokenRepository;
    private final BCryptPasswordEncoder passwordEncoder = new BCryptPasswordEncoder();

    @Transactional
    public AuthResponse login(LoginRequest request, String deviceKey) {
        Member member = memberRepository.findByEmailAndDeletedAtIsNull(request.email())
                .filter(m -> passwordEncoder.matches(request.password(), m.getPasswordHash()))
                .orElseThrow(() -> new GeneralException(AppErrorStatus.AUTH_BAD_CREDENTIALS));
        return signIn(member, deviceKey);
    }

    @Transactional
    public AuthResponse social(SocialLoginRequest request, String deviceKey) {
        if (request.provider() == Provider.EMAIL) {
            throw new GeneralException(ErrorStatus._BAD_REQUEST);
        }
        IdTokenVerifier.Identity identity = idTokenVerifier.verify(request.provider(), request.idToken())
                .orElseThrow(() -> new GeneralException(ErrorStatus._BAD_REQUEST));
        Member member = memberRepository
                .findByProviderAndProviderSubjectAndDeletedAtIsNull(request.provider(), identity.subject())
                .orElseGet(() -> {
                    String handle = newHandle();
                    return memberRepository.save(Member.social(request.provider(), identity.subject(),
                            identity.email(), handle.substring(1), handle));
                });
        return signIn(member, deviceKey);
    }

    @Transactional
    public AuthResponse signup(SignupRequest request, String deviceKey) {
        if (memberRepository.findByEmailAndDeletedAtIsNull(request.email()).isPresent()) {
            throw new GeneralException(AppErrorStatus.MEMBER_ALREADY_EXISTS);
        }
        Member member = memberRepository.save(Member.email(request.email(),
                passwordEncoder.encode(request.password()), request.nick().trim(), newHandle()));
        return signIn(member, deviceKey);
    }

    // 발송 수단이 아직 없다. 계정 존재만 확인하고 계약의 코드로 답한다.
    @Transactional(readOnly = true)
    public void requestPasswordReset(PasswordResetRequest request) {
        if (memberRepository.findByEmailAndDeletedAtIsNull(request.email()).isEmpty()) {
            throw new GeneralException(AppErrorStatus.MEMBER_NOT_FOUND);
        }
    }

    // 요청에 디바이스가 없어 기기 단위로 고를 수 없다. 회원의 리프레시 전부 폐기.
    @Transactional
    public void logout(AppPrincipal principal) {
        if (principal.isMember()) {
            refreshTokenRepository.revokeAll(principal.memberId(), Instant.now());
        }
    }

    @Transactional
    public AuthResponse refresh(RefreshRequest request) {
        Instant now = Instant.now();
        RefreshToken current = refreshTokenRepository.findByTokenHash(hash(request.refreshToken()))
                .filter(t -> t.usable(now))
                .orElseThrow(() -> new GeneralException(ErrorStatus._BAD_REQUEST));
        Member member = memberRepository.findByIdAndDeletedAtIsNull(current.getMemberId())
                .orElseThrow(() -> new GeneralException(ErrorStatus._BAD_REQUEST));
        current.revoke(now);
        return new AuthResponse(tokens.issue(member.getId()), issueRefresh(member.getId(), current.getDeviceId(), now),
                MeResponse.from(member), false);
    }

    // PRD 게스트 데이터 정책: 계정에 관심 데이터가 없으면 디바이스의 것을 계정으로 옮긴다. 디바이스는 회원에 연결.
    private AuthResponse signIn(Member member, String deviceKey) {
        long memberPrincipal = principalRepository.upsertMember(member.getId());
        Long deviceId = null;
        boolean mapped = false;
        if (deviceKey != null) {
            long devicePrincipal = principalRepository.upsertDevice(deviceKey);
            deviceId = principalRepository.findById(devicePrincipal).orElseThrow().getDeviceId();
            deviceRepository.link(deviceKey, member.getId());
            if (principalRepository.countOwnedData(memberPrincipal) == 0
                    && principalRepository.countOwnedData(devicePrincipal) > 0) {
                principalRepository.clearWatchData(memberPrincipal);
                principalRepository.transferOwnership(devicePrincipal, memberPrincipal);
                mapped = true;
            }
        }
        return new AuthResponse(tokens.issue(member.getId()), issueRefresh(member.getId(), deviceId, Instant.now()),
                MeResponse.from(member), mapped);
    }

    private String issueRefresh(long memberId, Long deviceId, Instant now) {
        byte[] raw = new byte[32];
        RANDOM.nextBytes(raw);
        String token = Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
        refreshTokenRepository.save(RefreshToken.issue(memberId, hash(token), deviceId, now.plus(REFRESH_TTL)));
        return token;
    }

    private String newHandle() {
        StringBuilder sb = new StringBuilder("@");
        for (int i = 0; i < 8; i++) {
            sb.append(HANDLE_ALPHABET.charAt(RANDOM.nextInt(HANDLE_ALPHABET.length())));
        }
        return sb.toString();
    }

    private static String hash(String token) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(token.getBytes()));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
