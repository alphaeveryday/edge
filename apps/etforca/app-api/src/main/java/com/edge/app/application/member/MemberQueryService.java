package com.edge.app.application.member;

import com.edge.app.application.member.provided.MemberFinder;
import com.edge.app.application.member.required.MemberRepository;
import com.edge.app.domain.member.Member;
import com.edge.common.apipayload.code.status.ErrorStatus;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class MemberQueryService implements MemberFinder {
    private final MemberRepository memberRepository;

    @Override
    @Transactional(readOnly = true)
    public Member find(long memberId) {
        return memberRepository.findByIdAndDeletedAtIsNull(memberId)
                .orElseThrow(() -> new GeneralException(ErrorStatus._UNAUTHORIZED));
    }

    @Override
    @Transactional(readOnly = true)
    public boolean isActive(long memberId) {
        return memberRepository.existsByIdAndDeletedAtIsNull(memberId);
    }
}
