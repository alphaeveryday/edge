region                   = "ap-northeast-2"
widget_api_image         = "393229433969.dkr.ecr.ap-northeast-2.amazonaws.com/edge/widget-api:0.0.1"
tenant_console_api_image = "393229433969.dkr.ecr.ap-northeast-2.amazonaws.com/edge/tenant-console-api:0.0.1"
super_admin_api_image    = "393229433969.dkr.ecr.ap-northeast-2.amazonaws.com/edge/super-admin-api:0.0.1"

# news-pipeline 워커 (ALPHA-304). 이미지는 구 CDK ECR 레포의 현행 태그 —
# 컷오버 전에 수동 생성한 edge/ 레포로 이미지를 복사하고 이 값을 교체한다(CDK 스택 삭제 시 구 레포 소멸 대비).
news_pipeline_image             = "393229433969.dkr.ecr.ap-northeast-2.amazonaws.com/news-pipeline/dev/pipeline:analysis-v1-202606240242"
news_pipeline_fmp_secret_arn    = "arn:aws:secretsmanager:ap-northeast-2:393229433969:secret:news-pipeline/dev/fmp/api-key-7yMtGt"
news_pipeline_openai_secret_arn = "arn:aws:secretsmanager:ap-northeast-2:393229433969:secret:news-pipeline/dev/openai/api-key-CctpWV"
