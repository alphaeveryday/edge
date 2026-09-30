package com.edge.app.member.repository;

import com.edge.app.member.entity.Member;
import com.edge.app.member.entity.Provider;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface MemberRepository extends JpaRepository<Member, Long> {
    Optional<Member> findByIdAndDeletedAtIsNull(long id);

    Optional<Member> findByHandleAndDeletedAtIsNull(String handle);

    Optional<Member> findByEmailAndDeletedAtIsNull(String email);

    Optional<Member> findByProviderAndProviderSubjectAndDeletedAtIsNull(Provider provider, String subject);

    boolean existsByHandle(String handle);
}
