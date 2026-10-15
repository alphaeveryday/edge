package com.edge.app.community.vote.event;

import com.edge.app.community.vote.repository.VoteRepository;
import com.edge.app.member.event.MemberWithdrawn;
import lombok.RequiredArgsConstructor;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

/** 탈퇴 회원의 투표 삭제 */
@Component
@RequiredArgsConstructor
public class VoteWithdrawListener {
    private final VoteRepository voteRepository;
    private final ApplicationEventPublisher eventPublisher;

    @EventListener
    public void on(MemberWithdrawn event) {
        var etfCodes = voteRepository.etfCodesOf(event.memberId());
        voteRepository.deleteByMember(event.memberId());
        eventPublisher.publishEvent(new VotesRemoved(etfCodes));
    }
}
