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
