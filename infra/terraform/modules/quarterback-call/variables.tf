variable "name" {
  description = "함수·역할 이름 (예: edge-dev-quarterback-call)"
  type        = string
}

variable "openai_secret_name" {
  description = "OPENAI_API_KEY 를 담은 Secrets Manager 시크릿 이름(값은 Terraform 밖에서 넣는다)"
  type        = string
}

variable "deepseek_secret_name" {
  description = "DEEPSEEK_API_KEY 를 담은 Secrets Manager 시크릿 이름"
  type        = string
}

variable "allowed_origins" {
  description = "호출을 받을 화면 Origin 목록 (예: https://quarterback.etforca.com)"
  type        = list(string)
}
