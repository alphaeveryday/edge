package com.edge.app;

import com.edge.app.service.VoteCommandService;
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
    VoteCommandService commandService;

    @Test
    void writeBehindBeansAreAbsentByDefault() {
        assertFalse(context.containsBean("writeBehindVoteCommandService"));
        assertFalse(context.containsBean("voteFlusher"));
        assertFalse(context.containsBean("voteWarmer"));
        assertTrue(context.containsBean("voteReconciler"));
        assertTrue(context.containsBean("voteCacheListener"));
        assertEquals("DbFirstVoteCommandService", AopUtils.getTargetClass(commandService).getSimpleName());
    }
}
