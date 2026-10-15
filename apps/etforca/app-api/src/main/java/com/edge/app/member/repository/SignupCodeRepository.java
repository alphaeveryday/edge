package com.edge.app.member.repository;

import com.edge.app.member.entity.SignupCode;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface SignupCodeRepository extends JpaRepository<SignupCode, String> {
    // 동시 확인·재발송이 시도 수를 덮어쓰지 않도록 행 잠금
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select c from SignupCode c where c.email = :email")
    Optional<SignupCode> findForUpdate(@Param("email") String email);

    @Modifying
    @Query("delete from SignupCode c where c.email = :email")
    void deleteByEmail(@Param("email") String email);
}
