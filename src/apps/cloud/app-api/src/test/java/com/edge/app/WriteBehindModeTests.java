package com.edge.app;

import com.edge.app.service.VoteCommandService;
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
    VoteCommandService commandService;

    @Test
    void dbFirstBeansAreAbsent() {
        assertFalse(context.containsBean("voteReconciler"));
        assertFalse(context.containsBean("voteAdminController"));
        assertFalse(context.containsBean("voteCacheListener"));
        assertFalse(context.containsBean("dbFirstVoteCommandService"));
    }

    @Test
    void commandServiceIsWriteBehind() {
        assertEquals("WriteBehindVoteCommandService", AopUtils.getTargetClass(commandService).getSimpleName());
    }
}
