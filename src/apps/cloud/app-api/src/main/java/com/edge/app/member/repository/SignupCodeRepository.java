package com.edge.app.member.repository;

import com.edge.app.member.entity.SignupCode;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface SignupCodeRepository extends JpaRepository<SignupCode, String> {
    @Modifying
    @Query("delete from SignupCode c where c.email = :email")
    void deleteByEmail(@Param("email") String email);
}
