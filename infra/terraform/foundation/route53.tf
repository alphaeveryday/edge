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

# ETF Orca 서비스 도메인(etforca.com). 등록은 가비아에서 했고 영역은 여기서 새로 만든다.
# 영역만 만들어서는 연결되지 않는다 — 출력값 etforca_name_servers 4개를 가비아의
# 네임서버 설정에 넣어야 한다(수동, 1회).
resource "aws_route53_zone" "etforca" {
  name = var.etforca_zone_name

  # 지웠다 다시 만들면 네임서버 4개가 바뀌어 가비아 설정도 다시 해야 한다.
  lifecycle {
    prevent_destroy = true
  }
}
