package com.edge.app.member.service;

import com.edge.app.member.dto.MeResponse;
import com.edge.app.member.dto.MeUpdateRequest;
import org.springframework.stereotype.Service;

/** 스텁. */
@Service
public class MemberService {
    public MeResponse me(long memberId) {
        return MemberExamples.me();
    }

    public MeResponse update(long memberId, MeUpdateRequest request) {
        return MemberExamples.me();
    }

    public void deleteAccount(long memberId) {
    }

    public MeResponse acceptDisclaimer(long memberId) {
        return MemberExamples.me();
    }
}
