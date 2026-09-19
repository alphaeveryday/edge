package com.edge.app;

import com.edge.app.service.VoteService;
import org.junit.jupiter.api.Test;
import org.springframework.aop.support.AopUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(properties = "vote.reconcile.initial-delay=PT1H")
class DefaultModeTests extends ContainerTests {
    @Autowired
    ApplicationContext context;
    @Autowired
    VoteService voteService;

    @Test
    void writeBehindBeansAreAbsentByDefault() {
        assertFalse(context.containsBean("writeBehindVoteService"));
        assertFalse(context.containsBean("voteFlusher"));
        assertTrue(context.containsBean("voteReconciler"));
        assertEquals("VoteService", AopUtils.getTargetClass(voteService).getSimpleName());
    }
}
