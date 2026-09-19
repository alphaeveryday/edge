package com.edge.app;

import com.edge.app.service.VoteService;
import org.junit.jupiter.api.Test;
import org.springframework.aop.support.AopUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

@SpringBootTest(properties = {"vote.mode=write-behind", "vote.flush.interval-ms=3600000"})
class WriteBehindModeTests extends ContainerTests {
    @Autowired
    ApplicationContext context;
    @Autowired
    VoteService voteService;

    @Test
    void reconcilerAndAdminControllerAreAbsent() {
        assertFalse(context.containsBean("voteReconciler"));
        assertFalse(context.containsBean("voteAdminController"));
    }

    @Test
    void voteServiceResolvesToWriteBehindVariant() {
        assertEquals("WriteBehindVoteService", AopUtils.getTargetClass(voteService).getSimpleName());
    }
}
