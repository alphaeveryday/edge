# ALPHA-632 — 인라인 콘솔 배선(구 console-site.tf)을 proxy-site 모듈로 이관하며 state 주소를
# 보존한다(배포 destroy+recreate 금지 — alias 충돌·다운타임 방지). mts_site 는 모듈 이름과
# 리소스 이름(aws_cloudfront_distribution.this·aws_route53_record.alias)이 구 static-site 와
# 같아 moved 가 필요 없다 — S3 계열(버킷·OAC·정책)만 config 에서 사라져 destroy 되며,
# 버킷은 비어 있지 않으면 destroy 가 실패하므로 apply 전에 비운다(aws s3 rm --recursive).
moved {
  from = aws_cloudfront_distribution.console
  to   = module.console_site.aws_cloudfront_distribution.this
}

moved {
  from = aws_route53_record.console_alias
  to   = module.console_site.aws_route53_record.alias
}
