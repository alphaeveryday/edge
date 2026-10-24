package com.edge.app.application.member.required;

import com.edge.app.domain.member.Member;
import com.edge.app.domain.member.Provider;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface MemberRepository extends JpaRepository<Member, Long> {
    Optional<Member> findByIdAndDeletedAtIsNull(long id);

    Optional<Member> findByHandleAndDeletedAtIsNull(String handle);

    Optional<Member> findByEmailAndDeletedAtIsNull(String email);

    Optional<Member> findByProviderAndProviderSubjectAndDeletedAtIsNull(Provider provider, String subject);

    boolean existsByHandle(String handle);
}
