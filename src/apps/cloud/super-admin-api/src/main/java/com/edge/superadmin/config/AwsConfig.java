package com.edge.superadmin.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.sfn.SfnClient;

/**
 * AWS 제어면 클라이언트(ALPHA-979 조각 2). 이 앱이 AWS 를 부르는 <b>유일한</b> 자리다.
 *
 * <p>⚠️ <b>기동을 막지 않는다.</b> 자격증명은 SDK 가 요청 시점에 푼다(태스크 역할). 그래서
 * 로컬·테스트처럼 자격이 없는 환경에서도 빈 생성은 성공하고, 실패는 <b>조회 시점</b>에
 * {@code RunControlPlane} 의 "제어면을 못 봤다"로 접힌다 — 콘솔 전체가 AWS 때문에 안 뜨는
 * 쪽이 더 나쁘다(ADR-0050: AWS 실패가 DB 축을 막지 않는다).
 *
 * <p>리전만은 명시한다. SDK 는 환경에서 리전을 못 찾으면 <b>빈 생성에서</b> 던지는데, 그러면
 * 위 규약이 깨져 기동 자체가 실패한다. ECS 가 {@code AWS_REGION} 을 넣어 주지만 그것에
 * 의존하지 않는다 — 못 찾으면 배포 리전으로 떨어진다.
 */
@Configuration
public class AwsConfig {

	@Bean
	SfnClient sfnClient(@Value("${aws.region:${AWS_REGION:ap-northeast-2}}") String region) {
		return SfnClient.builder().region(Region.of(region)).build();
	}
}
