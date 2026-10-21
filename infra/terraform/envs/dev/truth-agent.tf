# ── 투자정보 기만 검증 에이전트 (ADR-0062) ─────────────────
# 매시 사건 갱신(새 사건 찾기는 일정에 없다). 결과는 etforca.com/truth/ 의 공개 데이터(랜딩 버킷 truth/data/)로 게시된다.
# 이미지는 deploy-truth-agent.yml 이 edge/pipeline:truth-agent-latest 로 올린다(이 PR 보다 먼저 머지돼야 첫 실행이 뜬다).
# 키 값은 apply 뒤 TF 밖에서 넣는다 — 모듈 main.tf 의 시크릿 주석.
module "truth_agent" {
  source = "../../modules/truth-agent"

  name        = "${local.prefix}-truth-agent"
  region      = var.region
  vpc_id      = module.network.vpc_id
  subnet_ids  = module.network.private_subnet_ids
  cluster_arn = module.worker_cluster.cluster_arn
  image       = "${local.data_pipeline_ecr_repository_url}:truth-agent-latest"

  lake_bucket_name    = module.pipeline.lake_bucket
  lake_bucket_arn     = module.pipeline.lake_bucket_arn
  landing_bucket_name = module.landing_site.bucket_name
  landing_bucket_arn  = module.landing_site.bucket_arn

  alarm_topic_arn   = module.data_pipeline.alarm_topic_arn
  scheduler_dlq_arn = module.data_pipeline.scheduler_dlq_arn
}

output "truth_agent_store_bucket" { value = module.truth_agent.store_bucket }
output "truth_agent_runtime_secret_name" { value = module.truth_agent.runtime_secret_name }
