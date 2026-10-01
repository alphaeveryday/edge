package com.edge.app.community.post.event;

import com.edge.app.community.post.repository.PostLikeRepository;
import com.edge.app.member.event.MemberWithdrawn;
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
