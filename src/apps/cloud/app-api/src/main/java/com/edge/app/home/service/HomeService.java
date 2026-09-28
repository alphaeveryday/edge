package com.edge.app.home.service;

import com.edge.app.common.auth.AppPrincipal;
import com.edge.app.home.dto.HomeBriefResponse;
import org.springframework.stereotype.Service;

/** 스텁. */
@Service
public class HomeService {
    public HomeBriefResponse brief(AppPrincipal principal, String group) {
        return HomeExamples.brief(group);
    }
}
