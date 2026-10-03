# 전망 배치 대상은 수집 설정의 국내 ETF 목록 그대로다(손으로 옮겨 적지 않는다). 목록이 바뀌면
# terraform-plan·apply 가 이 파일 경로로도 깨어난다(워크플로 paths).
# 절 안의 주석·빈 줄이 아닌 **모든 줄**이 `6자리 코드 = …` 여야 한다(들여쓰기·따옴표 유무 무관).
# 아닌 줄은 invalid:… 로 남겨 모듈의 precondition 이 plan 을 실패시킨다 — 형식이 달라진 줄을
# 조용히 빼고 성공하지 않기 위해서다.
locals {
  sources_toml      = file("${path.module}/../../../../src/apps/cloud/data-pipeline/src/data_pipeline/config/sources.toml")
  etf_map_section   = split("\n[", split("[krx_etf.source.etf_map]\n", local.sources_toml)[1])[0]
  etf_map_lines     = [for line in split("\n", local.etf_map_section) : line if !can(regex("^\\s*(#.*)?$", line))]
  outlook_etf_codes = [for line in local.etf_map_lines : try(regex("^\\s*\"?([0-9A-Z]{6})\"?\\s*=", line)[0], "invalid:${trimspace(line)}")]
}

module "analysis_v2" {
  source                = "../../modules/analysis-v2"
  name                  = "edge-dev-analysis-v2"
  region                = var.region
  vpc_id                = module.network.vpc_id
  subnet_ids            = module.network.private_subnet_ids
  cluster_arn           = module.worker_cluster.cluster_arn
  db_security_group_id  = module.rds.security_group_id
  bucket_name           = module.pipeline.lake_bucket
  image                 = "${local.data_pipeline_ecr_repository_url}:analysis-v2-0c88464c8239b7db518a694851912b4840dc95c0"
  deploy_role_name      = element(split("/", module.gha_deploy_dev.role_arn), 1)
  operator_arn          = "arn:aws:iam::393229433969:user/junyoung727"
  api_image             = "${local.data_pipeline_ecr_repository_url}:analysis-v2-api-0c88464c8239b7db518a694851912b4840dc95c0"
  api_client_role_names = [element(split("/", module.app_api.task_role_arn), 1)]
  price_queue_url       = module.data_pipeline.minute_queue_urls["price-explanation-realtime"]
  price_queue_arn       = module.data_pipeline.minute_queue_arns["price-explanation-realtime"]

  # 분석 동시 3건. 연결 한도(V202610022100)와 슬롯을 읽는 워커 이미지가 dev 에 배포된 것을 확인한 뒤 올렸다(ALPHA-1157).
  analysis_slots = 3

  outlook_etf_codes = local.outlook_etf_codes
  alarm_topic_arn   = module.data_pipeline.alarm_topic_arn
  scheduler_dlq_arn = module.data_pipeline.scheduler_dlq_arn
  # 06:00 KST 시작, 08:00 KST 마감. 동시 3건으로 dev 37종 실측 81분 32초(2026-10-03, 37종 저장).
  # 여유와 한계는 tests/loadtest/analysis-v2/README.md '시작 시각'.
  outlook_schedule_expression = "cron(0 6 ? * MON-FRI *)"
  outlook_schedule_state      = "ENABLED"
}
output "analysis_v2_state_machine_arn" { value = module.analysis_v2.state_machine_arn }
output "analysis_v2_observer_role_arn" { value = module.analysis_v2.observer_role_arn }
output "analysis_v2_api_url" { value = module.analysis_v2.api_url }
output "analysis_v2_outlook_batch_state_machine_arn" { value = module.analysis_v2.outlook_batch_state_machine_arn }
