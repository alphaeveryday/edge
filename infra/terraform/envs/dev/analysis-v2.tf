module "analysis_v2" {
  source               = "../../modules/analysis-v2"
  name                 = "edge-dev-analysis-v2"
  region               = var.region
  vpc_id               = module.network.vpc_id
  subnet_ids           = module.network.private_subnet_ids
  cluster_arn          = module.worker_cluster.cluster_arn
  db_security_group_id = module.rds.security_group_id
  bucket_name          = module.pipeline.lake_bucket
  image                = "${local.data_pipeline_ecr_repository_url}:analysis-v2-bootstrap"
  deploy_role_name     = element(split("/", module.gha_deploy_dev.role_arn), 1)
  operator_arn         = "arn:aws:iam::393229433969:user/junyoung727"
}
output "analysis_v2_state_machine_arn" { value = module.analysis_v2.state_machine_arn }
output "analysis_v2_observer_role_arn" { value = module.analysis_v2.observer_role_arn }
