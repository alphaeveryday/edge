package com.edge.app.community.block.service;

import com.edge.app.common.AppErrorStatus;
import com.edge.app.community.block.repository.MemberBlockRepository;
import com.edge.app.member.entity.Member;
import com.edge.app.member.repository.MemberRepository;
import com.edge.common.exception.GeneralException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class BlockService {
    private final MemberRepository memberRepository;
    private final MemberBlockRepository blockRepository;

    // 멱등. 이미 차단했어도 성공
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
