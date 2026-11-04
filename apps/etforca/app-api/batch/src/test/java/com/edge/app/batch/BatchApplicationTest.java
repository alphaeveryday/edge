package com.edge.app.batch;

import org.junit.jupiter.api.Test;
import org.springframework.batch.core.BatchStatus;
import org.springframework.batch.core.job.Job;
import org.springframework.batch.core.job.JobExecution;
import org.springframework.batch.core.job.builder.JobBuilder;
import org.springframework.batch.core.job.parameters.JobParameters;
import org.springframework.batch.core.launch.JobOperator;
import org.springframework.batch.core.repository.JobRepository;
import org.springframework.batch.core.step.builder.StepBuilder;
import org.springframework.batch.infrastructure.repeat.RepeatStatus;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * app-api 마이그레이션이 만든 메타 테이블과 이 모듈 Spring Batch 버전의 JobRepository 정합
 * Batch 버전만 올리고 V22 를 원문과 맞추지 않으면 실패
 */
@BatchTest
class BatchApplicationTest {
    @Autowired
    JobOperator jobOperator;
    @Autowired
    Job probe;
    @Autowired
    JdbcTemplate jdbc;

    @Test
    void recordsJobExecutionInMetaTables() throws Exception {
        JobExecution execution = jobOperator.start(probe, new JobParameters());

        assertThat(execution.getStatus()).isEqualTo(BatchStatus.COMPLETED);
        assertThat(jdbc.queryForObject("select status from batch_step_execution where job_execution_id = ?",
                String.class, execution.getId())).isEqualTo("COMPLETED");
    }

    @TestConfiguration(proxyBeanMethods = false)
    static class Config {
        @Bean
        Job probe(JobRepository jobRepository, PlatformTransactionManager tx) {
            return new JobBuilder("probe", jobRepository)
                    .start(new StepBuilder("probe", jobRepository).tasklet((contribution, context) -> RepeatStatus.FINISHED, tx).build())
                    .build();
        }
    }
}
