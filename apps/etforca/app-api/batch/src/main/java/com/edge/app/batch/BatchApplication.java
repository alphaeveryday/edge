package com.edge.app.batch;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/** 잡 실행 뒤 종료하는 예약 태스크 진입점, 잡 결과의 프로세스 종료 코드 반영 */
@SpringBootApplication
public class BatchApplication {
    public static void main(String[] args) {
        System.exit(SpringApplication.exit(SpringApplication.run(BatchApplication.class, args)));
    }
}
