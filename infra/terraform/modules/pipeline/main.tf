# pipeline — 구 news-pipeline SFN 의 존치 자원(데이터 레이크)만 남긴 모듈.
#
# 원래는 Step Functions 로 오케스트레이션하던 일일 배치(CDK→edge 이관)였으나, 현행 파이프라인이
# edge-dev-data-pipeline(modules/data-pipeline)으로 전환되며 이 SFN 은 죽었다 — ALPHA-549 에서
# SFN·스케줄러·SNS·ECS task-def·fmp/openai 시크릿·IAM 을 모두 걷어냈다.
# 남은 유일한 live 자원은 lake S3 버킷: data-pipeline 이 raw/canonical/curated 를 담는 곳이라
# 모듈째 삭제하지 못하고 이 껍데기만 존치한다(레이크 소유권 이관은 별건).
#
# 파일 구성:
#   storage.tf  S3(single lake bucket) + 걷어낸 레거시 자원의 removed 블록
