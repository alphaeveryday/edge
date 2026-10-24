variable "name" {
  type = string
}
variable "region" {
  type = string
}
variable "cluster_arn" {
  type = string
}
variable "vpc_id" {
  type = string
}
variable "subnet_ids" {
  type = list(string)
}
variable "image" {
  type        = string
  description = "PuppyGraph 이미지. 다이제스트로 고정한다."
}
variable "username" {
  type = string
}
variable "password_parameter_arn" {
  type        = string
  description = "PUPPYGRAPH_PASSWORD 를 담은 SecureString SSM 매개변수. Terraform 밖에서 만든다."
}
variable "client_security_group_ids" {
  type        = list(string)
  description = "7687·8081 로 들어올 수 있는 보안 그룹."
}
variable "on_schedule" {
  type        = string
  description = "켜는 시각(Asia/Seoul)."
}
variable "off_schedule" {
  type        = string
  description = "끄는 시각(Asia/Seoul)."
}
variable "credentials_parameter_arn" {
  type        = string
  description = "SecureString SSM 매개변수 ARN. 값은 JSON이고 업로더는 db_username·db_password(그래프의 DB 읽기 계정)만 쓴다. Terraform 밖에서 만든다."
}
variable "schema_json" {
  type        = string
  description = "PuppyGraph 매핑(노드·엣지, 자격 증명 없음). 업로더가 기동마다 올린다."
}
variable "db_host" {
  type        = string
  description = "매핑이 읽는 뷰가 있는 PostgreSQL 호스트."
}
variable "loader_image" {
  type        = string
  description = "업로더 이미지(파이썬 표준 라이브러리만 쓴다). 다이제스트로 고정한다."
}
