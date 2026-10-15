package com.edge.app.member.repository;

import com.edge.app.member.entity.AppleToken;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AppleTokenRepository extends JpaRepository<AppleToken, Long> {
}
