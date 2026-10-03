package com.edge.app.notification.event;

import com.edge.app.community.block.repository.MemberBlockRepository;
import com.edge.app.community.post.event.ReplyCreated;
import com.edge.app.notification.service.NotificationService;
import lombok.RequiredArgsConstructor;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

/**
 * 답글 발생 시 글쓴이 comm 알림
 * 자기 글 답글과 글쓴이가 차단한 답글 작성자 제외
 * 같은 트랜잭션 처리
 */
@Component
@RequiredArgsConstructor
public class ReplyListener {
    private final NotificationService notificationService;
    private final MemberBlockRepository blockRepository;

    @EventListener
    public void on(ReplyCreated event) {
        if (event.postAuthorId() == event.replyAuthorId()
                || blockRepository.existsByBlockerIdAndBlockedId(event.postAuthorId(), event.replyAuthorId())) {
            return;
        }
        notificationService.notifyReply(event.postAuthorId(), event.postId(), event.body());
    }
}
