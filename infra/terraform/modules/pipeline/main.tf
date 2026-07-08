# news-pipeline — Step Functions 로 오케스트레이션되는 일일 배치 파이프라인.
# CDK(news-pipeline-dev-*) 를 Terraform 으로 이관하며, VPC·RDS 는 edge 스택으로 통합(A안).
# CDK 스택은 그대로 두고 이 스택은 별도 이름(edge-dev-pipeline-*)으로 병렬 생성한다 —
# 검증 후 스케줄러를 켜고(schedule_state=ENABLED) CDK 를 걷어내는 순서.
#
# 파이프라인: collect → alias_map → persist → fmp_price_collect → fmp_financial_collect
#           → us_news_ingest → compute_ff5 → analyze_daily(추론, 큰 태스크) → Succeed
# 각 스텝은 ecs:runTask.sync 로 Fargate task 를 동기 실행. 실패 시 SNS 알림 → Fail.
#
# 파일 구성:
#   storage.tf       S3(single lake bucket) · API 키 시크릿
#   iam.tf           실행/태스크/상태머신/스케줄러 IAM
#   tasks.tf         로그 · SG · 태스크 정의 2종(pipeline/inference)
#   statemachine.tf  SNS · Step Functions 정의/상태머신 · EventBridge Scheduler

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
}
