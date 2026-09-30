package com.edge.app.auth.service;

import com.edge.app.common.AppErrorStatus;
import com.edge.app.common.mail.Mailer;
import com.edge.app.member.entity.SignupCode;
import com.edge.app.member.repository.MemberRepository;
import com.edge.app.member.repository.SignupCodeRepository;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;

/** 가입 이메일 인증 코드 발송과 확인 */
@Service
@RequiredArgsConstructor
public class SignupCodeService {
    private static final Duration RESEND_GAP = Duration.ofSeconds(60);

    private final SignupCodeRepository codeRepository;
    private final MemberRepository memberRepository;
    private final Mailer mailer;

    // 60초 내 재요청은 발송 없이 같은 응답
    @Transactional
    public void send(String email) {
        if (memberRepository.findByEmailAndDeletedAtIsNull(email).isPresent()) {
            throw new GeneralException(AppErrorStatus.MEMBER_ALREADY_EXISTS);
        }
        Instant now = Instant.now();
        SignupCode current = codeRepository.findById(email).orElse(null);
        if (current != null && current.getCreatedAt().plus(RESEND_GAP).isAfter(now)) {
            return;
        }
        String code = AuthService.newCode();
        if (current == null) {
            codeRepository.save(SignupCode.issue(email, AuthService.hash(code), now));
        } else {
            current.reissue(AuthService.hash(code), now);
        }
        mailer.send(email, "[ETF Orca] 가입 인증 코드",
                "가입 인증 코드는 " + code + " 입니다.\n10분 안에 앱에 입력해 주세요.\n요청하지 않았다면 이 메일을 무시해 주세요.");
    }

    // 가입 트랜잭션과 분리해 틀린 시도 수를 확정 저장. 코드 삭제는 가입 성공 트랜잭션 몫
    @Transactional(propagation = Propagation.REQUIRES_NEW, noRollbackFor = GeneralException.class)
    public void verify(String email, String code) {
        Instant now = Instant.now();
        SignupCode saved = codeRepository.findById(email).filter(c -> c.usable(now))
                .orElseThrow(() -> new GeneralException(AppErrorStatus.AUTH_RESET_CODE_INVALID));
        if (!MessageDigest.isEqual(saved.getCodeHash().getBytes(), AuthService.hash(code).getBytes())) {
            saved.fail();
            throw new GeneralException(AppErrorStatus.AUTH_RESET_CODE_INVALID);
        }
    }
}
