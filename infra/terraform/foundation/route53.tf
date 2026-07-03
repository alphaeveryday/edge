# 퍼블릭 호스팅 영역. 도메인 등록(구매)만 수동이고, 영역부터는 TF 가 소유한다.
# 기존 영역을 import 로 채택 → NS 위임 보존, 무중단. env 는 이 영역을 data 로 읽어 레코드를 건다.
resource "aws_route53_zone" "main" {
  name = var.zone_name

  # DNS 루트는 인프라의 최상단. foundation destroy 로도 실수로 날리지 않게 막는다.
  lifecycle {
    prevent_destroy = true
  }
}

import {
  to = aws_route53_zone.main
  id = var.zone_id
}
