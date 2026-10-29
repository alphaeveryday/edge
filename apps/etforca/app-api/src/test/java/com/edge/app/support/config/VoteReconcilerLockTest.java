package com.edge.app.support.config;

import com.edge.app.adapter.webapi.vote.VoteAdminController;
import com.edge.app.application.vote.VoteReconciler;
import com.edge.app.application.vote.required.VoteCountStore;
import com.edge.app.application.vote.required.VoteReconcileResult;
import com.edge.app.application.vote.required.VoteRepository;
import io.lettuce.core.event.Event;
import io.lettuce.core.event.EventBus;
import io.lettuce.core.event.connection.ConnectionActivatedEvent;
import io.lettuce.core.resource.ClientResources;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import net.javacrumbs.shedlock.core.LockProvider;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.test.util.ReflectionTestUtils;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import reactor.core.publisher.Sinks;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@Testcontainers
class VoteReconcilerLockTest {
    @Container
    static final PostgreSQLContainer postgres = new PostgreSQLContainer("postgres:16");

    private final List<Node> nodes = new ArrayList<>();
    private DriverManagerDataSource dataSource;

    @BeforeEach
    void createLockTable() {
        dataSource = new DriverManagerDataSource(postgres.getJdbcUrl(), postgres.getUsername(), postgres.getPassword());
        var jdbc = new org.springframework.jdbc.core.JdbcTemplate(dataSource);
        jdbc.execute("drop table if exists shedlock");
        new ResourceDatabasePopulator(new ClassPathResource("db/etf-migration/V2__shedlock.sql")).execute(dataSource);
    }

    @AfterEach
    void closeNodes() throws Exception {
        for (var node : nodes) {
            node.reconciler.close();
            assertThat(worker(node).awaitTermination(5, TimeUnit.SECONDS)).isTrue();
        }
    }

    @ParameterizedTest
    @EnumSource(Trigger.class)
    void allTriggersExcludeAnotherInstanceFromSnapshotThroughLastReplacement(Trigger ownerTrigger) throws Exception {
        // 독립 executor와 JDBC LockProvider 두 개가 같은 PostgreSQL 리스를 공유한다.
        var owner = node(provider());
        var contender = node(provider());
        var snapshotEntered = new CountDownLatch(1);
        var releaseSnapshot = new CountDownLatch(1);
        var lastReplacementEntered = new CountDownLatch(1);
        var releaseLastReplacement = new CountDownLatch(1);
        var replacements = new AtomicInteger();
        when(owner.repository.findReconcileRows()).thenAnswer(invocation -> {
            snapshotEntered.countDown();
            await(releaseSnapshot);
            return rows();
        });
        when(owner.store.replace(anyString(), anyList())).thenAnswer(invocation -> {
            if (replacements.incrementAndGet() == 2) {
                lastReplacementEntered.countDown();
                await(releaseLastReplacement);
            }
            return new VoteReconcileResult(0, 0, 0);
        });
        Trigger competingTrigger = Trigger.values()[(ownerTrigger.ordinal() + 1) % Trigger.values().length];
        try {
            trigger(owner, ownerTrigger);
            await(snapshotEntered);
            assertThat(owner.reconciler.request()).as("같은 인스턴스의 겹친 요청은 병합").isFalse();
            trigger(contender, competingTrigger);
            drain(contender);
            verifyNoInteractions(contender.repository, contender.store);

            releaseSnapshot.countDown();
            await(lastReplacementEntered);
            trigger(contender, competingTrigger);
            drain(contender);
            verifyNoInteractions(contender.repository, contender.store);
            assertThat(contender.metrics.find("vote.reconcile.success").counter()).isNull();
        } finally {
            releaseSnapshot.countDown();
            releaseLastReplacement.countDown();
            drain(owner);
        }
        // 잠금 경합으로 건너뛴 인스턴스도 이전 실행이 끝난 뒤에는 다시 요청할 수 있다.
        trigger(contender, competingTrigger);
        drain(contender);
        verify(contender.repository).findReconcileRows();
        verify(contender.store, times(2)).replace(anyString(), anyList());
        assertThat(contender.metrics.counter("vote.reconcile.success").count()).isEqualTo(1);
    }

    @Test
    void lockProviderFailureSkipsSnapshotAndAllowsTheNextRequest() throws Exception {
        var attempts = new AtomicInteger();
        var delegate = provider();
        var node = node(configuration -> {
            if (attempts.getAndIncrement() == 0) {
                throw new IllegalStateException("lock database unavailable");
            }
            return delegate.lock(configuration);
        });
        assertThat(node.reconciler.request()).isTrue();
        drain(node);
        verifyNoInteractions(node.repository, node.store);
        assertThat(node.metrics.counter("vote.reconcile.failures").count()).isEqualTo(1);
        assertThat(node.reconciler.request()).isTrue();
        drain(node);
        verify(node.repository).findReconcileRows();
        assertThat(node.metrics.counter("vote.reconcile.success").count()).isEqualTo(1);
    }

    @Test
    void snapshotFailureReleasesTheLeaseForAnotherInstance() throws Exception {
        var failed = node(provider());
        var next = node(provider());
        when(failed.repository.findReconcileRows()).thenThrow(new IllegalStateException("snapshot failed"));
        assertThat(failed.reconciler.request()).isTrue();
        drain(failed);
        verifyNoInteractions(failed.store);
        assertThat(failed.metrics.counter("vote.reconcile.failures").count()).isEqualTo(1);
        assertThat(next.reconciler.request()).isTrue();
        drain(next);
        verify(next.store, times(2)).replace(anyString(), anyList());
        assertThat(next.metrics.counter("vote.reconcile.success").count()).isEqualTo(1);
    }

    @Test
    void cacheFailureReleasesTheLeaseAfterTryingTheRemainingEtfs() throws Exception {
        var failed = node(provider());
        var next = node(provider());
        doThrow(new IllegalStateException("one Redis shard failed")).when(failed.store).replace("000001", List.of());
        assertThat(failed.reconciler.request()).isTrue();
        drain(failed);
        verify(failed.store, times(2)).replace(anyString(), anyList());
        assertThat(failed.metrics.counter("vote.reconcile.failures").count()).isEqualTo(1);
        assertThat(next.reconciler.request()).isTrue();
        drain(next);
        verify(next.store, times(2)).replace(anyString(), anyList());
        assertThat(next.metrics.counter("vote.reconcile.success").count()).isEqualTo(1);
    }

    private LockProvider provider() {
        return new SchedulerLockConfig().lockProvider(dataSource);
    }

    private Node node(LockProvider provider) {
        var repository = mock(VoteRepository.class);
        var store = mock(VoteCountStore.class);
        var metrics = new SimpleMeterRegistry();
        var resources = mock(ClientResources.class);
        var eventBus = mock(EventBus.class);
        var events = Sinks.many().multicast().<Event>onBackpressureBuffer();
        when(resources.eventBus()).thenReturn(eventBus);
        when(eventBus.get()).thenReturn(events.asFlux());
        var rows = rows();
        when(repository.findReconcileRows()).thenReturn(rows);
        when(store.replace(anyString(), anyList())).thenReturn(new VoteReconcileResult(0, 0, 0));
        var reconciler = new VoteReconciler(repository, store, metrics, resources, provider);
        reconciler.subscribe();
        var node = new Node(reconciler, repository, store, metrics, events);
        nodes.add(node);
        return node;
    }

    private List<VoteRepository.ReconcileRow> rows() {
        return List.of(row("000001"), row("000002"));
    }

    private VoteRepository.ReconcileRow row(String code) {
        var row = mock(VoteRepository.ReconcileRow.class);
        when(row.getEtfCode()).thenReturn(code);
        return row;
    }

    private void trigger(Node node, Trigger trigger) {
        switch (trigger) {
            case SCHEDULE -> node.reconciler.scheduled();
            case RECONNECT -> assertThat(node.events.tryEmitNext(mock(ConnectionActivatedEvent.class))).isEqualTo(Sinks.EmitResult.OK);
            case ADMIN -> {
                var controller = new VoteAdminController(node.reconciler);
                ReflectionTestUtils.setField(controller, "adminToken", "test-admin");
                controller.reconcile("test-admin");
            }
        }
    }

    private ExecutorService worker(Node node) {
        return (ExecutorService) ReflectionTestUtils.getField(node.reconciler, "executor");
    }

    private void drain(Node node) throws Exception {
        worker(node).submit(() -> {}).get(5, TimeUnit.SECONDS);
    }

    private void await(CountDownLatch latch) throws InterruptedException {
        assertThat(latch.await(5, TimeUnit.SECONDS)).isTrue();
    }

    private enum Trigger { SCHEDULE, RECONNECT, ADMIN }

    private record Node(VoteReconciler reconciler, VoteRepository repository, VoteCountStore store,
                        SimpleMeterRegistry metrics, Sinks.Many<Event> events) {}
}
