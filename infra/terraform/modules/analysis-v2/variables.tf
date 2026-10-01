variable "name" { type = string }
variable "region" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "cluster_arn" { type = string }
variable "db_security_group_id" { type = string }
variable "bucket_name" { type = string }
variable "image" { type = string }
variable "deploy_role_name" { type = string }
variable "operator_arn" { type = string }
variable "api_image" { type = string }
variable "api_client_role_names" {
  type    = list(string)
  default = []
}
