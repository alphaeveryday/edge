package com.edge.app.application.notification;

import com.edge.app.application.member.provided.AppPrincipal;
import com.edge.app.application.member.required.PrincipalRepository;
import com.edge.app.application.notification.provided.NotificationFinder;
import com.edge.app.application.notification.required.NotificationRepository;
import com.edge.app.domain.notification.NotiFilter;
import com.edge.app.domain.notification.NotiKind;
import com.edge.app.domain.notification.Notification;
import com.edge.app.support.cursor.Cursor;
import com.edge.app.support.cursor.PageResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Limit;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;

@Service
@RequiredArgsConstructor
public class NotificationQueryService implements NotificationFinder {
    private static final Cursor START = new Cursor(Instant.parse("9999-12-31T00:00:00Z"), Long.MAX_VALUE);

    private final PrincipalRepository principalRepository;
    private final NotificationRepository repository;

    @Override
    @Transactional
    public PageResponse<Notification> list(AppPrincipal principal, NotiFilter filter, Cursor cursor, int size) {
        long principalId = principalRepository.resolve(principal);
        NotiKind kind = filter == null ? null : filter.kind();
        Cursor from = cursor == null ? START : cursor;
        List<Notification> rows = repository.page(principalId, from.createdAt(), from.id(), kind != null,
                kind == null ? NotiKind.WATCH : kind, Limit.of(size + 1));
        boolean more = rows.size() > size;
        List<Notification> visible = more ? rows.subList(0, size) : rows;
        Notification last = visible.isEmpty() ? null : visible.get(visible.size() - 1);
        return new PageResponse<>(visible, more ? new Cursor(last.getCreatedAt(), last.getId()).encode() : null);
    }

    @Override
    @Transactional
    public long unread(AppPrincipal principal) {
        return repository.countByPrincipalIdAndReadAtIsNull(principalRepository.resolve(principal));
    }
}
