package com.edge.app.common.mail;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDate;

public interface MailDailyRepository extends JpaRepository<MailDaily, LocalDate> {
    @Query(value = """
            insert into mail_daily (day, sent) values (:day, 1)
            on conflict (day) do update set sent = mail_daily.sent + 1
            returning sent
            """, nativeQuery = true)
    int increment(@Param("day") LocalDate day);
}
