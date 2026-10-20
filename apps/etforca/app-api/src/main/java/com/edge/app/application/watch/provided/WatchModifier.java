package com.edge.app.application.watch.provided;

import com.edge.app.application.member.provided.AppPrincipal;

/**
 * 관심 그룹과 종목 구성 변경
 */
public interface WatchModifier {
    WatchGroupResponse createGroup(AppPrincipal principal, WatchGroupCreateRequest request);

    void deleteGroup(AppPrincipal principal, String key);

    void setMembers(AppPrincipal principal, String key, WatchMembersRequest request);

    void setMembership(AppPrincipal principal, String code, WatchMembershipRequest request);
}
