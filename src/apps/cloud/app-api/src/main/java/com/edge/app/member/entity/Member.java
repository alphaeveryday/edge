package com.edge.app.member.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.Instant;

@Entity
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Member {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(length = 255)
    private String email;

    @Column(name = "password_hash", length = 100)
    private String passwordHash;

    @Column(length = 10, nullable = false)
    private Provider provider;

    @Column(name = "provider_subject", length = 255)
    private String providerSubject;

    @Column(length = 30, nullable = false)
    private String nick;

    @Column(length = 30, nullable = false)
    private String handle;

    @Column(name = "disclaimer_accepted_at")
    private Instant disclaimerAcceptedAt;

    @Column(name = "deleted_at")
    private Instant deletedAt;

    public static Member email(String email, String passwordHash, String nick, String handle) {
        Member member = new Member();
        member.email = email;
        member.passwordHash = passwordHash;
        member.provider = Provider.EMAIL;
        member.nick = nick;
        member.handle = handle;
        return member;
    }

    public static Member social(Provider provider, String subject, String email, String nick, String handle) {
        Member member = new Member();
        member.provider = provider;
        member.providerSubject = subject;
        member.email = email;
        member.nick = nick;
        member.handle = handle;
        return member;
    }

    public void update(String nick, String handle) {
        if (nick != null) {
            this.nick = nick;
        }
        if (handle != null) {
            this.handle = handle;
        }
    }

    public void changePassword(String passwordHash) {
        this.passwordHash = passwordHash;
    }

    public void acceptDisclaimer(Instant at) {
        this.disclaimerAcceptedAt = at;
    }

    /** 탈퇴 처리. 행 유지, 식별 정보만 NULL, 재가입 가능 */
    public void withdraw(Instant at) {
        this.deletedAt = at;
        this.email = null;
        this.passwordHash = null;
        this.providerSubject = null;
    }
}
