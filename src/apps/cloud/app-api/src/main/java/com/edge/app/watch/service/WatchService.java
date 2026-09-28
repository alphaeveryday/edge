package com.edge.app.watch.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.etf.dto.EtfSummaryResponse;
import com.edge.app.etf.service.EtfExamples;
import com.edge.app.watch.dto.WatchGroupCreateRequest;
import com.edge.app.watch.dto.WatchGroupResponse;
import com.edge.app.watch.dto.WatchMembersRequest;
import com.edge.app.watch.dto.WatchMembershipRequest;
import org.springframework.stereotype.Service;

import java.util.List;

/** 스텁. */
@Service
public class WatchService {
    public List<WatchGroupResponse> groups(AppPrincipal principal) {
        return List.of(WatchExamples.base());
    }

    public WatchGroupResponse createGroup(AppPrincipal principal, WatchGroupCreateRequest request) {
        return new WatchGroupResponse("key", request.label(), 0);
    }

    public void deleteGroup(AppPrincipal principal, String group) {
    }

    public List<EtfSummaryResponse> list(AppPrincipal principal, String group) {
        return List.of(EtfExamples.summary());
    }

    public void setMembers(AppPrincipal principal, String group, WatchMembersRequest request) {
    }

    public List<String> membership(AppPrincipal principal, String code) {
        return List.of(WatchExamples.base().key());
    }

    public void setMembership(AppPrincipal principal, String code, WatchMembershipRequest request) {
    }
}
