package com.edge.app.notification.event;

import com.edge.app.community.post.event.ReplyCreated;
import com.edge.app.notification.service.NotificationService;
import lombok.RequiredArgsConstructor;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

/** 답글이 달리면 글쓴이에게 comm 알림. 자기 글에 단 답글은 알리지 않는다. 같은 트랜잭션에서 처리된다. */
@Component
@RequiredArgsConstructor
public class ReplyListener {
    private final NotificationService notificationService;

    @EventListener
    public void on(ReplyCreated event) {
        if (event.postAuthorId() == event.replyAuthorId()) {
            return;
        }
        notificationService.notifyReply(event.postAuthorId(), event.postId(), event.body());
    }
}
