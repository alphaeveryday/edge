package com.edge.app.application.post;

import com.edge.app.application.post.required.PostLikeRepository;
import com.edge.app.domain.member.MemberWithdrawn;
import lombok.RequiredArgsConstructor;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

/** 탈퇴 회원의 좋아요 삭제와 글 좋아요 수 차감 */
@Component
@RequiredArgsConstructor
public class LikeWithdrawListener {
    private final PostLikeRepository postLikeRepository;

    @EventListener
    public void on(MemberWithdrawn event) {
        postLikeRepository.deleteByMember(event.memberId());
    }
}
