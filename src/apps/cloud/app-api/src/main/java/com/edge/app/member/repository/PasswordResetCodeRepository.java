package com.edge.app.member.repository;

import com.edge.app.member.entity.PasswordResetCode;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface PasswordResetCodeRepository extends JpaRepository<PasswordResetCode, Long> {
    // 동시 확인·재발송이 시도 수를 덮어쓰지 않도록 행 잠금
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select c from PasswordResetCode c where c.memberId = :memberId")
    Optional<PasswordResetCode> findForUpdate(@Param("memberId") long memberId);
}
