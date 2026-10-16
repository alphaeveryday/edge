package com.edge.app.application.vote;

import com.edge.app.application.vote.required.VoteRepository;
import com.edge.app.domain.member.MemberWithdrawn;
import com.edge.app.domain.vote.VotesRemoved;
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
