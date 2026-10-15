package com.edge.app.notification.event;

import com.edge.app.community.block.repository.MemberBlockRepository;
import com.edge.app.community.post.event.CommentCreated;
import com.edge.app.community.post.event.CommentDeleted;
import com.edge.app.notification.service.NotificationService;
import lombok.RequiredArgsConstructor;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

/**
 * 댓글 발생 시 글쓴이 comm 알림
 * 자기 글 댓글과 글쓴이가 차단한 댓글 작성자 제외
 * 댓글 삭제 시 그 댓글 알림의 삭제
 * 같은 트랜잭션 처리
 */
@Component
@RequiredArgsConstructor
public class CommentListener {
    private final NotificationService notificationService;
    private final MemberBlockRepository blockRepository;

    @EventListener
    public void on(CommentCreated event) {
        if (event.postAuthorId() == event.commentAuthorId()
                || blockRepository.existsByBlockerIdAndBlockedId(event.postAuthorId(), event.commentAuthorId())) {
            return;
        }
        notificationService.notifyComment(event.postAuthorId(), event.postId(), event.commentId(), event.body());
    }

    @EventListener
    public void on(CommentDeleted event) {
        notificationService.removeComment(event.commentId());
    }
}
