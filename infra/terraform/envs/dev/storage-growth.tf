# 데이터 레이크 버킷이 하루에 크게 늘면 알린다. 2026-11 분석 실행 기록(analysis-v2/runs, 117.7GiB)과 DB 백업이 쌓인 것을
# 사람이 따로 재 보고서야 알았다. S3 크기 지표(BucketSizeBytes)는 하루 1번 나오므로 하루 차이를 본다.
# DIFF 는 앞 값이 있어야 나오므로 평가 구간을 이틀로 두고 그중 하루가 넘으면 울린다.
# 5GiB: 정상일 때 하루 증가(뉴스·공시·장중 원본, 분석 기록 30일 만료 후)보다 크고, 11-19~24 이상 증가(하루 2.6GiB+)와
# 비슷하거나 큰 증가를 하루 안에 잡는 자리다.
resource "aws_cloudwatch_metric_alarm" "lake_daily_growth" {
  alarm_name          = "${local.prefix}-lake-daily-growth"
  alarm_description   = "데이터 레이크 버킷(${module.pipeline.lake_bucket})이 하루에 5GiB 넘게 늘었다. 할 일: aws s3 ls --recursive --summarize 로 접두사별 크기를 비교해 늘어난 곳을 찾고, 수명 규칙(modules/pipeline/storage.tf)이 필요한지 본다."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 1
  threshold           = 5368709120 # 5 GiB
  treat_missing_data  = "notBreaching"
  alarm_actions       = [module.data_pipeline.alarm_topic_arn]

  metric_query {
    id          = "growth"
    expression  = "DIFF(size)"
    label       = "lake bytes added per day"
    return_data = true
  }

  metric_query {
    id = "size"
    metric {
      namespace   = "AWS/S3"
      metric_name = "BucketSizeBytes"
      period      = 86400
      stat        = "Average"
      dimensions = {
        BucketName  = module.pipeline.lake_bucket
        StorageType = "StandardStorage"
      }
    }
  }
}
