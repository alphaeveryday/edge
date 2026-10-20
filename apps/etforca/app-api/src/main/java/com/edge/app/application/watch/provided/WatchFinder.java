package com.edge.app.application.watch.provided;

import com.edge.app.application.etf.provided.EtfSummaryResponse;
import com.edge.app.application.member.provided.AppPrincipal;

import java.util.List;

/**
 * 관심 그룹 조회
 * - 첫 접근 시 기본 그룹 생성
 */
public interface WatchFinder {
    List<WatchGroupResponse> groups(AppPrincipal principal);

    List<EtfSummaryResponse> list(AppPrincipal principal, String key);

    List<String> membership(AppPrincipal principal, String code);
}
