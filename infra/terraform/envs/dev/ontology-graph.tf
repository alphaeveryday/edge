# 그래프 도구의 PuppyGraph(ALPHA-1342). 평일 05:30 KST에 켜서(06:00 전망 배치 전) 16:00 KST에 끈다(사용자 결정 2026-10-14).
# 하루 10.5시간 × 평일 약 22일 × 시간당 약 0.175달러(2 vCPU·16GB) = 월 약 40달러.
# 주말 배치(06:00 매일) 시간에는 꺼져 있다. 그래프 도구는 아직 운영 분석 경로에 붙지 않았다.
# 비밀번호 매개변수는 Terraform 밖에서 만든다. 매핑은 같은 작업의 업로더가 기동마다 올린다.
module "ontology_graph" {
  source       = "../../modules/ontology-graph"
  name         = "edge-dev-ontology-graph"
  region       = var.region
  cluster_arn  = module.worker_cluster.cluster_arn
  vpc_id       = module.network.vpc_id
  subnet_ids   = module.network.private_subnet_ids
  image        = "puppygraph/puppygraph@sha256:8e362b158d8dde1ae9ed6df54f6751630af72a47d061bb2db42fa16547b368f7"
  username     = "ontology"
  on_schedule  = "cron(30 5 ? * MON-FRI *)"
  off_schedule = "cron(0 16 ? * MON-FRI *)"

  password_parameter_arn = "arn:aws:ssm:${var.region}:393229433969:parameter/edge/dev/ontology-graph/password"
  # DB 계정(ontology_graph_ro)과 관리자 비밀번호가 든 JSON. 10/5 시험 때 만든 매개변수를 그대로 쓴다(Terraform 밖).
  credentials_parameter_arn = "arn:aws:ssm:${var.region}:393229433969:parameter/edge/dev/ontology-puppygraph-pilot/credentials"
  # 매핑 원본은 온톨로지 라이브러리에 있다. 바뀌면 이 파일이 apply 경로(terraform-apply.yml paths)에 걸려 새 작업 정의가 된다.
  schema_json  = file("${path.root}/../../../../libs/ontology/src/edge_ontology/sql/puppygraph-schema.json")
  db_host      = module.rds.address
  loader_image = "public.ecr.aws/docker/library/python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704"
  # 지금은 SSM 터널용 임시 바스천(edge-dev-tmp-bastion-view, Terraform 밖)만. 분석 워커를 붙일 때 워커 보안 그룹을 더한다.
  # 바스천을 없앨 때는 이 목록에서 먼저 빼고 apply 한다(규칙이 남아 있으면 보안 그룹 삭제가 막힌다).
  client_security_group_ids = ["sg-0d7392109fd270848"]
  # 국내 급등주 조사의 그래프 도구(ALPHA-1453) — 조회만
  query_client_security_group_ids = { truth_research = module.truth_research.security_group_id }
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_ontology_graph" {
  security_group_id            = module.rds.security_group_id
  referenced_security_group_id = module.ontology_graph.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "ontology graph to postgres"
}

output "ontology_graph_service_name" { value = module.ontology_graph.service_name }
output "ontology_graph_bolt_uri" { value = module.ontology_graph.bolt_uri }
