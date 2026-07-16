# analysis-engine 은 저장소·시크릿을 소유하지 않는다 — 모두 외부에서 주입(느슨한 결합):
#
#   - lake bucket     : pipeline 모듈 소유(edge-dev-pipeline-lake). var.lake_bucket_name/arn 로 주입.
#                       canonical 뉴스를 읽고 설명 결과를 result prefix 에 쓴다.
#   - DeepSeek API 키  : data-pipeline 네임스페이스의 기존 시크릿
#                       (edge-dev-data-pipeline/deepseek/api-key). var.deepseek_secret_arn 으로 주입하고
#                       ':api_key::' 로 DEEPSEEK_API_KEY 에 읽힌다. 값은 TF 밖 수동 주입.
#   - DB 비밀번호       : RDS 관리형 시크릿({username,password}). var.db_password_secret_arn 으로 주입하고
#                       ':password::' 로 PGPASSWORD 에 읽힌다.
#
# 따라서 이 모듈은 aws_s3_bucket·aws_secretsmanager_secret 리소스를 만들지 않는다. 시크릿 주입
# 메커니즘(ECS secrets valueFrom)과 최소권한 IAM 은 data-pipeline 모듈과 동일하다(tasks.tf·iam.tf).
