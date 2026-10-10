package com.edge.app.auth.service;

import com.edge.app.auth.dto.AuthResponse;
import com.edge.app.auth.dto.LoginRequest;
import com.edge.app.auth.dto.PasswordResetConfirmRequest;
import com.edge.app.auth.dto.PasswordResetRequest;
import com.edge.app.auth.dto.RefreshRequest;
import com.edge.app.auth.dto.SignupRequest;
import com.edge.app.auth.dto.SocialLoginRequest;
import com.edge.app.common.AppErrorStatus;
import com.edge.app.common.auth.AccessTokens;
import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.common.mail.MailQuota;
import com.edge.app.common.mail.Mailer;
import com.edge.app.member.dto.MeResponse;
import com.edge.app.member.entity.Member;
import com.edge.app.member.entity.PasswordResetCode;
import com.edge.app.member.entity.Provider;
import com.edge.app.member.entity.RefreshToken;
import com.edge.app.member.repository.DeviceRepository;
import com.edge.app.member.repository.MemberRepository;
import com.edge.app.member.repository.PasswordResetCodeRepository;
import com.edge.app.member.repository.PrincipalRepository;
import com.edge.app.member.repository.RefreshTokenRepository;
import com.edge.app.member.repository.SignupCodeRepository;
import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Duration;
import java.time.Instant;
import java.util.Base64;
import java.util.HexFormat;

/**
  * 가입·로그인의 member 행 생성
  * 이후 회원 상태 변경의 MemberService 소관
  * 불투명 난수 리프레시 토큰의 sha-256 해시 저장
  * 재발급마다 리프레시 토큰 회전
 */
@Service
@RequiredArgsConstructor
public class AuthService {
    private static final Duration REFRESH_TTL = Duration.ofDays(30);
    private static final Duration RESET_RESEND_GAP = Duration.ofSeconds(60);
    private static final String HANDLE_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";
    private static final SecureRandom RANDOM = new SecureRandom();

    private final AccessTokens tokens;
    private final IdTokenVerifier idTokenVerifier;
    private final MemberRepository memberRepository;
    private final PrincipalRepository principalRepository;
    private final DeviceRepository deviceRepository;
    private final RefreshTokenRepository refreshTokenRepository;
    private final PasswordResetCodeRepository resetCodeRepository;
    private final SignupCodeRepository signupCodeRepository;
    private final Mailer mailer;
    private final MailQuota mailQuota;
    private final ReviewAccount reviewAccount;
    private final TransactionTemplate tx;
    private final BCryptPasswordEncoder passwordEncoder = new BCryptPasswordEncoder();

    // 소셜 가입 이메일의 가입 방법 안내
    @Transactional
    public AuthResponse login(LoginRequest request, String deviceKey) {
        Member member = memberRepository.findByEmailAndDeletedAtIsNull(request.email()).orElse(null);
        if (member != null && member.getProvider() != Provider.EMAIL) {
            throw new GeneralException(joinedWith(member.getProvider()));
        }
        if (member == null || !passwordEncoder.matches(request.password(), member.getPasswordHash())) {
            throw new GeneralException(AppErrorStatus.AUTH_BAD_CREDENTIALS);
        }
        return signIn(member, deviceKey, false);
    }

    // 트랜잭션 밖의 토큰 검증
    // 동시 첫 로그인의 유니크 위반은 먼저 생긴 회원으로 재시도
    public AuthResponse social(SocialLoginRequest request, String deviceKey) {
        if (request.provider() == Provider.EMAIL) {
            throw new GeneralException(ErrorStatus._BAD_REQUEST);
        }
        IdTokenVerifier.Identity identity = idTokenVerifier.verify(request.provider(), request.idToken(), request.nonce())
                .orElseThrow(() -> new GeneralException(AppErrorStatus.AUTH_SOCIAL_INVALID));
        try {
            return tx.execute(s -> socialSignIn(request.provider(), identity, deviceKey));
        } catch (DataIntegrityViolationException e) {
            return tx.execute(s -> socialSignIn(request.provider(), identity, deviceKey));
        }
    }

    // 다른 가입 방법의 같은 이메일은 가입 대신 안내
    private AuthResponse socialSignIn(Provider provider, IdTokenVerifier.Identity identity, String deviceKey) {
        Member existing = memberRepository.findByProviderAndProviderSubjectAndDeletedAtIsNull(provider, identity.subject())
                .orElse(null);
        if (existing != null) {
            return signIn(existing, deviceKey, false);
        }
        if (identity.email() != null) {
            memberRepository.findByEmailAndDeletedAtIsNull(identity.email()).ifPresent(m -> {
                throw new GeneralException(joinedWith(m.getProvider()));
            });
        }
        String handle = newHandle();
        Member member = memberRepository.saveAndFlush(Member.social(provider, identity.subject(), identity.email(),
                handle.substring(1), handle));
        return signIn(member, deviceKey, true);
    }

    // SignupCodeService.verify 의 인증 코드 확인 뒤 호출
    // 동시 가입의 유니크 위반도 MEMBER4002 응답
    @Transactional
    public AuthResponse signup(SignupRequest request, String deviceKey) {
        Member member;
        try {
            member = memberRepository.saveAndFlush(Member.email(request.email(),
                    passwordEncoder.encode(request.password()), request.nick().trim(), newHandle()));
        } catch (DataIntegrityViolationException e) {
            throw new GeneralException(AppErrorStatus.MEMBER_ALREADY_EXISTS);
        }
        signupCodeRepository.deleteByEmail(request.email());
        return signIn(member, deviceKey, true);
    }

    // 60초 내 재요청에 발송 없는 같은 응답
    // 가입 응답이 이미 드러내는 가입 여부라 은닉 생략
    @Transactional
    public void requestPasswordReset(PasswordResetRequest request) {
        Member member = memberRepository.findByEmailAndDeletedAtIsNull(request.email())
                .orElseThrow(() -> new GeneralException(AppErrorStatus.MEMBER_EMAIL_NOT_FOUND));
        if (member.getProvider() != Provider.EMAIL) {
            throw new GeneralException(joinedWith(member.getProvider()));
        }
        Instant now = Instant.now();
        PasswordResetCode current = resetCodeRepository.findForUpdate(member.getId()).orElse(null);
        if (current != null && current.getCreatedAt().plus(RESET_RESEND_GAP).isAfter(now)) {
            return;
        }
        boolean review = reviewAccount.is(member.getEmail());
        if (!review && current != null && current.dailyLimitReached(now)) {
            throw new GeneralException(AppErrorStatus.AUTH_MAIL_LIMIT);
        }
        if (!review) {
            mailQuota.take();
        }
        String code = review ? ReviewAccount.CODE : newCode();
        if (current == null) {
            resetCodeRepository.save(PasswordResetCode.issue(member.getId(), hash(code), now));
        } else {
            current.reissue(hash(code), now);
        }
        if (review) {
            return;
        }
        mailer.send(member.getEmail(), "[ETF Orca] 비밀번호 재설정 코드",
                "비밀번호 재설정 코드는 " + code + " 입니다.\n10분 안에 앱에 입력해 주세요.\n요청하지 않았다면 이 메일을 무시해 주세요.");
    }

    // 성공 시 코드 삭제와 리프레시 전부 폐기
    @Transactional(noRollbackFor = GeneralException.class)
    public void confirmPasswordReset(PasswordResetConfirmRequest request) {
        Instant now = Instant.now();
        Member member = memberRepository.findByEmailAndDeletedAtIsNull(request.email())
                .orElseThrow(() -> new GeneralException(AppErrorStatus.AUTH_RESET_CODE_INVALID));
        PasswordResetCode code = resetCodeRepository.findForUpdate(member.getId())
                .filter(c -> c.usable(now))
                .orElseThrow(() -> new GeneralException(AppErrorStatus.AUTH_RESET_CODE_INVALID));
        if (!MessageDigest.isEqual(code.getCodeHash().getBytes(), hash(request.code()).getBytes())) {
            code.fail();
            throw new GeneralException(AppErrorStatus.AUTH_RESET_CODE_INVALID);
        }
        member.changePassword(passwordEncoder.encode(request.newPassword()));
        resetCodeRepository.delete(code);
        refreshTokenRepository.revokeAll(member.getId(), now);
    }

    // 요청에 디바이스 정보가 없어 회원 리프레시 전부 폐기
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
                MeResponse.from(member), false, false);
    }

    // 디바이스의 회원 연결
    // 계정에 관심 데이터가 없을 때의 디바이스 소유 행 이전
    private AuthResponse signIn(Member member, String deviceKey, boolean newMember) {
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
                MeResponse.from(member), mapped, newMember);
    }

    private String issueRefresh(long memberId, Long deviceId, Instant now) {
        byte[] raw = new byte[32];
        RANDOM.nextBytes(raw);
        String token = Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
        refreshTokenRepository.save(RefreshToken.issue(memberId, hash(token), deviceId, now.plus(REFRESH_TTL)));
        return token;
    }

    // 같은 이메일의 다른 가입 방법 안내 코드
    static AppErrorStatus joinedWith(Provider provider) {
        return switch (provider) {
            case EMAIL -> AppErrorStatus.MEMBER_JOINED_WITH_EMAIL;
            case APPLE -> AppErrorStatus.MEMBER_JOINED_WITH_APPLE;
            case GOOGLE -> AppErrorStatus.MEMBER_JOINED_WITH_GOOGLE;
            case KAKAO -> AppErrorStatus.MEMBER_JOINED_WITH_KAKAO;
        };
    }

    private String newHandle() {
        StringBuilder sb = new StringBuilder("@");
        for (int i = 0; i < 8; i++) {
            sb.append(HANDLE_ALPHABET.charAt(RANDOM.nextInt(HANDLE_ALPHABET.length())));
        }
        return sb.toString();
    }

    static String newCode() {
        return "%06d".formatted(RANDOM.nextInt(1_000_000));
    }

    static String hash(String token) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(token.getBytes()));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
