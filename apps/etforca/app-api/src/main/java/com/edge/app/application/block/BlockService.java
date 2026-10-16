package com.edge.app.application.block;

import com.edge.app.application.block.required.MemberBlockRepository;
import com.edge.app.application.member.required.MemberRepository;
import com.edge.app.domain.member.Member;
import com.edge.app.support.exception.AppErrorStatus;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class BlockService {
    private final MemberRepository memberRepository;
    private final MemberBlockRepository blockRepository;

    // 이미 차단한 경우에도 성공하는 멱등 처리
    @Transactional
    public void block(long memberId, String handle) {
        Member target = memberRepository.findByHandleAndDeletedAtIsNull(handle)
                .orElseThrow(() -> new GeneralException(AppErrorStatus.MEMBER_NOT_FOUND));
        if (target.getId() == memberId) {
            throw new GeneralException(AppErrorStatus.MEMBER_SELF_BLOCK);
        }
        blockRepository.insertIfAbsent(memberId, target.getId());
    }
}
