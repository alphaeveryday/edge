package com.edge.app.watch.service;

import com.edge.app.watch.dto.WatchGroupResponse;

/** 스텁 고정값. 실구현 때 삭제. */
public final class WatchExamples {
    private WatchExamples() {
    }

    public static WatchGroupResponse base() {
        return new WatchGroupResponse("base", "label", 1);
    }
}
