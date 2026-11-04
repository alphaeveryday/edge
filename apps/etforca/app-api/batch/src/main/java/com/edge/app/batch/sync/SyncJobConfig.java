package com.edge.app.batch.sync;

import com.edge.app.batch.sync.SyncRepository.Curation;
import lombok.extern.slf4j.Slf4j;
import org.springframework.batch.core.ExitStatus;
import org.springframework.batch.core.job.Job;
import org.springframework.batch.core.job.builder.JobBuilder;
import org.springframework.batch.core.job.parameters.RunIdIncrementer;
import org.springframework.batch.core.listener.SkipListener;
import org.springframework.batch.core.listener.StepExecutionListener;
import org.springframework.batch.core.repository.JobRepository;
import org.springframework.batch.core.step.Step;
import org.springframework.batch.core.step.StepExecution;
import org.springframework.batch.core.step.builder.StepBuilder;
import org.springframework.batch.core.step.skip.AlwaysSkipItemSkipPolicy;
import org.springframework.batch.infrastructure.repeat.RepeatStatus;
import org.springframework.batch.infrastructure.support.transaction.ResourcelessTransactionManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.transaction.PlatformTransactionManager;

import java.util.List;

/**
 * 선별 ETF 의 파이프라인 RDS 데이터를 앱 테이블로 옮기는 동기화 잡
 * 실행마다 전체 재동기화라 새 인스턴스로 시작, 재시작 없음
 * ETF step 이 완료가 아니면(반영 0건 포함) 순위·지수 갱신 없이 잡 실패
 */
@Slf4j
@Configuration(proxyBeanMethods = false)
class SyncJobConfig {

    @Bean
    Job syncJob(JobRepository jobRepository, Step cleanupStep, Step etfStep, Step rankStep, Step indexStep) {
        return new JobBuilder("syncJob", jobRepository)
                .incrementer(new RunIdIncrementer())
                .start(cleanupStep)
                .next(etfStep)
                .on(ExitStatus.COMPLETED.getExitCode()).to(rankStep)
                .next(indexStep)
                .from(etfStep).on("*").fail()
                .end()
                .build();
    }

    // 선별 목록 밖 코드의 동기화 행 삭제
    // 빈 선별 목록의 전체 삭제 방지
    @Bean
    Step cleanupStep(JobRepository jobRepository, PlatformTransactionManager tx, SyncRepository sync) {
        return new StepBuilder("cleanupStep", jobRepository)
                .tasklet((contribution, context) -> {
                    List<String> codes = sync.curation().stream().map(Curation::code).toList();
                    if (codes.isEmpty()) {
                        throw new IllegalStateException("sync curation empty");
                    }
                    sync.deleteOutside(codes);
                    return RepeatStatus.FINISHED;
                }, tx)
                .build();
    }

    // ETF 하나당 한 트랜잭션
    // ETF 하나의 실패가 나머지를 막지 않는 skip
    @Bean
    Step etfStep(JobRepository jobRepository, PlatformTransactionManager tx, CurationReader curationReader,
            EtfProcessor processor, EtfWriter writer) {
        return new StepBuilder("etfStep", jobRepository)
                .<Curation, EtfSnapshot>chunk(1)
                .transactionManager(tx)
                .reader(curationReader)
                .processor(processor)
                .writer(writer)
                .faultTolerant()
                .skipPolicy(new AlwaysSkipItemSkipPolicy())
                .skipListener(new SkipListener<Curation, EtfSnapshot>() {
                    @Override
                    public void onSkipInProcess(Curation item, Throwable t) {
                        log.warn("sync failed code={}", item.code(), t);
                    }

                    @Override
                    public void onSkipInWrite(EtfSnapshot item, Throwable t) {
                        log.warn("sync failed code={}", item.curation().code(), t);
                    }
                })
                .listener(new StepExecutionListener() {
                    @Override
                    public ExitStatus afterStep(StepExecution step) {
                        log.info("sync done etfs={}/{} skipped={}", step.getWriteCount(), step.getReadCount(), step.getSkipCount());
                        // 스키마 불일치·원천 오접속 같은 전면 장애가 skip·필터에 묻혀 성공으로 끝나지 않도록 반영 0건의 실패 판정
                        if (step.getReadCount() > 0 && step.getWriteCount() == 0) {
                            log.error("sync failed reason=no-etf-written read={} skipped={} filtered={}",
                                    step.getReadCount(), step.getSkipCount(), step.getFilterCount());
                            return ExitStatus.FAILED;
                        }
                        return step.getExitStatus();
                    }
                })
                .build();
    }

    @Bean
    Step rankStep(JobRepository jobRepository, PlatformTransactionManager tx, RankTasklet tasklet) {
        return new StepBuilder("rankStep", jobRepository).tasklet(tasklet, tx).build();
    }

    // 한 지수의 SQL 오류가 같은 트랜잭션의 다른 지수 쓰기를 무효화하지 않는 문장 단위 자동 커밋
    @Bean
    Step indexStep(JobRepository jobRepository, IndexTasklet tasklet) {
        return new StepBuilder("indexStep", jobRepository).tasklet(tasklet, new ResourcelessTransactionManager()).build();
    }
}
