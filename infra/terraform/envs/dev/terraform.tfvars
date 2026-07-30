region                = "ap-northeast-2"
super_admin_api_image = "393229433969.dkr.ecr.ap-northeast-2.amazonaws.com/edge/super-admin-api:0.0.1"
# 최초 이미지는 deploy-tenant-sync-api 수동 실행(workflow_dispatch)이 0.0.1 로 push 한다 — 그 전까지 태스크는 pull 실패로 기동 대기.
tenant_sync_api_image = "393229433969.dkr.ecr.ap-northeast-2.amazonaws.com/edge/tenant-sync-api:0.0.1"
