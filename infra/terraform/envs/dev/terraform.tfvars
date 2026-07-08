region                   = "ap-northeast-2"
widget_api_image         = "393229433969.dkr.ecr.ap-northeast-2.amazonaws.com/edge/widget-api:0.0.1"
tenant_console_api_image = "393229433969.dkr.ecr.ap-northeast-2.amazonaws.com/edge/tenant-console-api:0.0.1"
super_admin_api_image    = "393229433969.dkr.ecr.ap-northeast-2.amazonaws.com/edge/super-admin-api:0.0.1"
gateway_image            = "393229433969.dkr.ecr.ap-northeast-2.amazonaws.com/edge/gateway:0.0.1"

# foundation 의 edge/pipeline 레포에 레포 소스로 빌드·push 후 태그 갱신(현재 placeholder).
pipeline_image = "393229433969.dkr.ecr.ap-northeast-2.amazonaws.com/edge/pipeline:latest"

# 기존 edge/data-pipeline 레포. deploy-data-pipeline.yml 이 latest 와 git sha 태그를 push 한다.
data_pipeline_image = "393229433969.dkr.ecr.ap-northeast-2.amazonaws.com/edge/data-pipeline:latest"
