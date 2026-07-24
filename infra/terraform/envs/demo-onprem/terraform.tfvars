region     = "ap-northeast-2"
mts_domain = "demo-mts.edgesignal.dev"

# 이 계정은 그린필드 IaC 라 default VPC 가 없다 → 박스를 둘 public 서브넷을 명시해야 한다.
# 여기 고정하지 않으면 매 apply 에 -var subnet_id=... 를 줘야 하고, 빠뜨리면 "no default VPC" 로 실패한다.
# dev VPC(edge-dev)의 public 서브넷(AZ-a) 재사용 — 데모 state 는 격리이나 네트워크는 dev VPC 를 빌린다.
subnet_id = "subnet-0fb30bfa3f1004918"

# 시연 기본은 t3.large. 부하 실험 시 크게 + 루트 볼륨 io2 로 승격:
#   instance_type    = "c7i.2xlarge"
#   root_volume_type = "io2"
#   root_volume_iops = 5000
