# ── 오늘 오른 종목 조사 에이전트 (docs/design/truth-movers.md) ─────────────────
# 장중 매시 :35 (truth-agent :05 와 30분 차이). 결과는 etforca.com/truth/today 의 공개 데이터(랜딩 버킷 truth/data/movers/)로 게시된다.
# 이미지는 deploy-truth-research.yml 이 edge/pipeline:truth-research-latest 로 올린다(이 PR 머지 때 같이 돈다 — 첫 실행 전에 이미지가 있어야 한다).
# 키는 truth-agent 시크릿을 그대로 쓴다(따로 넣을 값이 없다).
module "truth_research" {
  source = "../../modules/truth-research"

  name        = "${local.prefix}-truth-research"
  region      = var.region
  vpc_id      = module.network.vpc_id
  subnet_ids  = module.network.private_subnet_ids
  cluster_arn = module.worker_cluster.cluster_arn
  image       = "${local.data_pipeline_ecr_repository_url}:truth-research-latest"

  store_bucket_name   = module.truth_agent.store_bucket
  store_bucket_arn    = module.truth_agent.store_bucket_arn
  runtime_secret_arn  = module.truth_agent.runtime_secret_arn
  landing_bucket_name = module.landing_site.bucket_name
  landing_bucket_arn  = module.landing_site.bucket_arn
}
