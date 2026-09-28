package com.edge.app.notification.repository;

import com.edge.app.notification.entity.NotiKind;
import com.edge.app.notification.entity.Notification;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.List;

public interface NotificationRepository extends JpaRepository<Notification, Long> {
    /** 최신순 키셋. 첫 페이지의 at·id 는 상한 sentinel. byKind 면 kind 로 거른다. */
    @Query("""
            select n from Notification n where n.principalId = :p
              and (n.createdAt < :at or (n.createdAt = :at and n.id < :id))
              and (:byKind = false or n.kind = :kind)
            order by n.createdAt desc, n.id desc
            """)
    List<Notification> page(@Param("p") long principalId, @Param("at") Instant at, @Param("id") long id,
            @Param("byKind") boolean byKind, @Param("kind") NotiKind kind, Limit limit);

    long countByPrincipalIdAndReadAtIsNull(long principalId);

    @Modifying
    @Query("update Notification n set n.readAt = :at where n.id = :id and n.principalId = :p and n.readAt is null")
    void markRead(@Param("p") long principalId, @Param("id") long id, @Param("at") Instant at);

    @Modifying
    @Query("update Notification n set n.readAt = :at where n.principalId = :p and n.readAt is null")
    void markAllRead(@Param("p") long principalId, @Param("at") Instant at);
}
