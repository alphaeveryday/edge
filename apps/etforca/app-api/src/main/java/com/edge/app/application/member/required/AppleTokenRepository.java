package com.edge.app.application.member.required;

import com.edge.app.domain.member.AppleToken;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AppleTokenRepository extends JpaRepository<AppleToken, Long> {
}
