# ── 오늘 오른 종목 조사 에이전트 (docs/design/truth-movers.md) ─────────────────
# 장중 매시 :35 (truth-agent :05 와 30분 차이). 결과는 etforca.com/truth/today 의 공개 데이터(랜딩 버킷 truth/data/movers/, 미국 movers/us/)로 게시된다.
# 미국은 뉴욕 시간대 장중 매시 :35·매분(설계 §6).
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

  # 국내 보도는 운영 DB 에서 읽는다(ALPHA-1450) — 분석 엔진의 읽기 전용 계정을 함께 쓴다(사용자 결정 2026-10-24)
  db_host              = module.rds.address
  db_secret_arn        = data.aws_secretsmanager_secret.analysis_v2_reader.arn
  db_security_group_id = module.rds.security_group_id

  # 국내 공시 원문 — KIND 뷰어가 AWS IP 를 막아 DART 에서 받는다. data-pipeline 의 OpenDART 키를 함께 쓴다(ALPHA-1457)
  dart_secret_arn = module.data_pipeline.dart_secret_arn

  # 국내 종목 사건 그래프(ALPHA-1453)
  graph_uri                    = module.ontology_graph.bolt_uri
  graph_security_group_id      = module.ontology_graph.security_group_id
  graph_password_parameter_arn = "arn:aws:ssm:${var.region}:393229433969:parameter/edge/dev/ontology-graph/password"

  # 미국 공시(SEC) 요청 연락처 — truth 사이트 처리방침(privacy.html)에 공개한 운영 연락처와 같다
  sec_contact = "asm.alphaeveryday@gmail.com"
}

data "aws_secretsmanager_secret" "analysis_v2_reader" { name = "edge/analysis-v2/readonly" }

resource "aws_vpc_security_group_ingress_rule" "rds_from_truth_research" {
  security_group_id            = module.rds.security_group_id
  referenced_security_group_id = module.truth_research.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "truth-research to postgres (read-only news)"
}
