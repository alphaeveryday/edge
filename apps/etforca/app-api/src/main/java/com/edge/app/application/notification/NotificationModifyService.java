package com.edge.app.application.notification;

import com.edge.app.application.member.provided.AppPrincipal;
import com.edge.app.application.member.required.PrincipalRepository;
import com.edge.app.application.notification.provided.NotificationModifier;
import com.edge.app.application.notification.required.NotificationRepository;
import com.edge.app.domain.notification.Notification;
import com.edge.app.domain.notification.PushRequested;
import lombok.RequiredArgsConstructor;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.Map;

@Service
@RequiredArgsConstructor
public class NotificationModifyService implements NotificationModifier {
    private static final int PUSH_BODY_MAX = 100;

    private final PrincipalRepository principalRepository;
    private final NotificationRepository repository;
    private final ApplicationEventPublisher eventPublisher;

    // 남의 알림과 잘못된 id 의 no-op 처리
    // 404 가 없는 계약
    @Override
    @Transactional
    public void read(AppPrincipal principal, String id) {
        long principalId = principalRepository.resolve(principal);
        try {
            repository.markRead(principalId, Long.parseLong(id), Instant.now());
        } catch (NumberFormatException ignored) {
        }
    }

    @Override
    @Transactional
    public void readAll(AppPrincipal principal) {
        repository.markAllRead(principalRepository.resolve(principal), Instant.now());
    }

    /** 글쓴이 회원 principal 이 없으면 생성까지 하는 댓글 알림 적재와 커밋 뒤 푸시 */
    @Transactional
    public void notifyComment(long postAuthorMemberId, long postId, long commentId, String commentBody) {
        long principalId = principalRepository.upsertMember(postAuthorMemberId);
        repository.save(Notification.comm(principalId, postId, commentId, "새 댓글", commentBody, Instant.now()));
        String preview = commentBody.length() > PUSH_BODY_MAX ? commentBody.substring(0, PUSH_BODY_MAX) + "…" : commentBody;
        eventPublisher.publishEvent(new PushRequested(principalId, "새 댓글", preview, Map.of("postId", Long.toString(postId))));
    }

    @Transactional
    public void removeComment(long commentId) {
        repository.deleteByCommentId(commentId);
    }
}
