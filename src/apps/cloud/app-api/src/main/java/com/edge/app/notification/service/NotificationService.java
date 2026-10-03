package com.edge.app.notification.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.common.cursor.Cursor;
import com.edge.app.common.cursor.PageResponse;
import com.edge.app.member.repository.PrincipalRepository;
import com.edge.app.notification.dto.NotificationResponse;
import com.edge.app.notification.dto.UnreadCountResponse;
import com.edge.app.notification.entity.NotiFilter;
import com.edge.app.notification.entity.NotiKind;
import com.edge.app.notification.entity.Notification;
import com.edge.app.notification.repository.NotificationRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Limit;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;

@Service
@RequiredArgsConstructor
public class NotificationService {
    private static final Cursor START = new Cursor(Instant.parse("9999-12-31T00:00:00Z"), Long.MAX_VALUE);

    private final PrincipalRepository principalRepository;
    private final NotificationRepository repository;

    @Transactional
    public PageResponse<NotificationResponse> list(AppPrincipal principal, NotiFilter filter, Cursor cursor, int size) {
        long principalId = principalRepository.resolve(principal);
        NotiKind kind = filter == null ? null : filter.kind();
        Cursor from = cursor == null ? START : cursor;
        List<Notification> rows = repository.page(principalId, from.createdAt(), from.id(), kind != null,
                kind == null ? NotiKind.WATCH : kind, Limit.of(size + 1));
        boolean more = rows.size() > size;
        List<Notification> visible = more ? rows.subList(0, size) : rows;
        Notification last = visible.isEmpty() ? null : visible.get(visible.size() - 1);
        return new PageResponse<>(visible.stream().map(NotificationService::response).toList(),
                more ? new Cursor(last.getCreatedAt(), last.getId()).encode() : null);
    }

    @Transactional
    public UnreadCountResponse unread(AppPrincipal principal) {
        return new UnreadCountResponse((int) repository.countByPrincipalIdAndReadAtIsNull(principalRepository.resolve(principal)));
    }

    // 남의 알림과 잘못된 id 의 no-op 처리
    // 404 가 없는 계약
    @Transactional
    public void read(AppPrincipal principal, String id) {
        long principalId = principalRepository.resolve(principal);
        try {
            repository.markRead(principalId, Long.parseLong(id), Instant.now());
        } catch (NumberFormatException ignored) {
        }
    }

    @Transactional
    public void readAll(AppPrincipal principal) {
        repository.markAllRead(principalRepository.resolve(principal), Instant.now());
    }

    /** 글쓴이 회원 principal 이 없으면 생성까지 하는 답글 알림 적재 */
    @Transactional
    public void notifyReply(long postAuthorMemberId, long postId, String replyBody) {
        long principalId = principalRepository.upsertMember(postAuthorMemberId);
        repository.save(Notification.comm(principalId, postId, "새 답글", replyBody, Instant.now()));
    }

    private static NotificationResponse response(Notification n) {
        return new NotificationResponse(Long.toString(n.getId()), n.getKind(), n.getEtfCode(),
                n.getPostId() == null ? null : Long.toString(n.getPostId()), n.getCreatedAt(), n.getTitle(), n.getBody(),
                n.getReadAt() != null);
    }
}
