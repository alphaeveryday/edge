# Airflow 실행 환경 — ECS on EC2(일반 ASG + Capacity Provider. ECS Managed Instances 아님).
#
# 구성(ALPHA-1119, README `src/apps/cloud/airflow` "실행 환경"):
# - 전용 클러스터 하나 + EC2 1대(ASG 1~2). 업무 클러스터(worker)는 건드리지 않는다 — 그 클러스터의
#   capacity provider 목록은 aws_ecs_cluster_capacity_providers 가 **통째로** 소유해서, EC2 CP 를 더하면
#   기존 리소스를 고치게 된다. 클러스터는 무료라 분리해도 비용이 같다.
# - ECS 서비스 1개, 태스크 1개 안에 api-server·scheduler·dag-processor 세 컨테이너(awsvpc, localhost 공유).
#   LocalExecutor 의 task 프로세스는 scheduler 컨테이너 안에서 돌고, Execution API 를 localhost:8080 으로 부른다.
#   triggerer 없음 — EdgeStep 은 deferrable=False 로 고정돼 있다(defer 경로가 판정을 건너뛴다).
# - 실제 업무는 기존 Fargate 태스크(worker 클러스터)가 한다. 이 호스트는 실행 관리만.
# - 메타DB 는 별도 RDS(env 쪽 module "airflow_rds"), 로그는 CloudWatch, DAG 는 이미지에 구워 넣는다 —
#   EC2 로컬 디스크에는 정본이 없다. 호스트가 사라져도 새 호스트에서 그대로 복구된다.

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  # awsvpc 태스크 안의 컨테이너 셋은 localhost 를 공유한다.
  api_port = 8080
  # 서비스 태스크 메모리(MiB). t4g.medium 등록 메모리(약 3.8GiB) 안에서 호스트·ECS 에이전트 몫을 남긴다.
  # 컨테이너 합 = api 900 + scheduler 1600 + dag-processor 600 = 3100. 로컬 실측 근거는 README "구성요소별 자원".
  task_memory = 3100
}

# ── 클러스터와 EC2 용량 ─────────────────────────────────
resource "aws_ecs_cluster" "this" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = "disabled" # 서비스 중단 알람은 기본 지표(AWS/ECS CPUUtilization)로 충분하다
  }
}

resource "aws_security_group" "host" {
  name        = "${var.name}-host"
  description = "Airflow EC2 host ${var.name} (no ingress; SSM only)"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-host" }
}

resource "aws_vpc_security_group_egress_rule" "host_all" {
  security_group_id = aws_security_group.host.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "ECS agent, SSM, ECR via NAT"
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "host" {
  name               = "${var.name}-host"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# ECS 에이전트(클러스터 등록·이미지 pull 은 태스크 execution 역할이 따로 한다) + SSM(UI 포트 포워딩·셸).
resource "aws_iam_role_policy_attachment" "host_ecs" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_role_policy_attachment" "host_ssm" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "host" {
  name = "${var.name}-host"
  role = aws_iam_role.host.name
}

resource "aws_launch_template" "host" {
  name_prefix   = "${var.name}-"
  image_id      = var.ami_id
  instance_type = var.instance_type

  iam_instance_profile {
    arn = aws_iam_instance_profile.host.arn
  }
  vpc_security_group_ids = [aws_security_group.host.id]

  # IMDSv2 강제. 태스크(awsvpc)는 호스트 역할 자격증명에 닿지 않게 한다(ECS_AWSVPC_BLOCK_IMDS).
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = var.root_volume_gib
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  user_data = base64encode(<<-EOT
    #!/bin/bash
    cat >> /etc/ecs/ecs.config <<'CFG'
    ECS_CLUSTER=${aws_ecs_cluster.this.name}
    ECS_AWSVPC_BLOCK_IMDS=true
    ECS_ENABLE_CONTAINER_METADATA=true
    CFG
  EOT
  )

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.name}-host" }
  }
}

# 평시 1대. 최대 2대는 호스트 교체(instance refresh) 때 새 호스트를 먼저 띄우는 자리다 — 평시 과금 없음.
resource "aws_autoscaling_group" "host" {
  name                = "${var.name}-host"
  min_size            = 1
  max_size            = 2
  desired_capacity    = 1
  vpc_zone_identifier = var.subnet_ids
  health_check_type   = "EC2"

  launch_template {
    id      = aws_launch_template.host.id
    version = aws_launch_template.host.latest_version
  }

  # managed draining 이 쓰는 태그. capacity provider 가 desired 를 조정한다.
  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }

  lifecycle {
    ignore_changes = [desired_capacity]
  }
}

resource "aws_ecs_capacity_provider" "host" {
  name = var.name

  auto_scaling_group_provider {
    auto_scaling_group_arn = aws_autoscaling_group.host.arn
    # 스케일인 보호를 쓰지 않는다 — 켜면 ASG 에 protect_from_scale_in 이 필요하고, 호스트 교체 때
    # 옛 호스트가 보호에 걸려 남는다. 대신 managed draining 이 종료 전에 태스크를 옮긴다.
    managed_termination_protection = "DISABLED"
    managed_draining               = "ENABLED"

    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = 100
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 1
    }
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  # FARGATE 도 연결한다 — 마이그레이션 one-off 와 격리 검증 태스크가 이 클러스터에서 Fargate 로 돈다(목록은 이 리소스가
  # 통째로 소유하므로 빠진 공급자는 연결되지 않는다). 서비스 기본 전략은 EC2 호스트 그대로다.
  capacity_providers = [aws_ecs_capacity_provider.host.name, "FARGATE"]

  default_capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.host.name
    weight            = 1
  }
}

# ── 로그 ────────────────────────────────────────────────
# 구성요소 stdout(awslogs)과 task 로그(Airflow remote logging)를 나눈다. 둘 다 CloudWatch 가 정본이다.
resource "aws_cloudwatch_log_group" "components" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "tasks" {
  name              = "/airflow/${var.name}/tasks"
  retention_in_days = var.log_retention_days
}

# ── 비밀값 ──────────────────────────────────────────────
# 값은 TF 가 만들지 않는다(state 에 평문을 남기지 않는다 — pipeline 시크릿과 같은 관례, ALPHA-312).
# 최초 1회 README "최초 구축" 절차로 넣는다: {"jwt_secret","api_secret_key","admin_password"}.
resource "aws_secretsmanager_secret" "airflow" {
  name        = "${var.name}/app"
  description = "Airflow API 서명키(jwt)·세션키·UI admin 비밀번호"
}

# ── 네트워크(태스크) ────────────────────────────────────
resource "aws_security_group" "task" {
  name        = "${var.name}-task"
  description = "Airflow components ${var.name}"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-task" }
}

resource "aws_vpc_security_group_egress_rule" "task_all" {
  security_group_id = aws_security_group.task.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "metadata DB, AWS APIs via NAT"
}

# UI 접근은 SSM 포트 포워딩뿐이다: 운영자 → (SSM) → 호스트 → 태스크 ENI:8080. 공개 진입점(ALB·공인 IP) 없음.
resource "aws_vpc_security_group_ingress_rule" "task_api_from_host" {
  security_group_id            = aws_security_group.task.id
  referenced_security_group_id = aws_security_group.host.id
  ip_protocol                  = "tcp"
  from_port                    = local.api_port
  to_port                      = local.api_port
  description                  = "Airflow UI/API via SSM port forwarding on the host"
}

resource "aws_vpc_security_group_ingress_rule" "db_from_airflow" {
  security_group_id            = var.db_security_group_id
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = var.db_port
  to_port                      = var.db_port
  description                  = "Airflow components to metadata DB"
}

# ── IAM(태스크) ─────────────────────────────────────────
data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# execution: 이미지 pull·awslogs·시크릿 주입(메타DB 비밀번호, Airflow 앱 시크릿).
resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "${var.name}-execution-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [var.db_password_secret_arn, aws_secretsmanager_secret.airflow.arn]
    }]
  })
}

# task: Airflow 프로세스(scheduler 의 LocalExecutor task 포함)가 쓰는 권한 — 업무 ECS 제출·추적, SNS 통보,
# 업무 컨테이너 로그 조회, 자기 task 로그 쓰기. 업무 데이터(S3·DB)에는 권한이 없다.
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

locals {
  batch_taskdef_arns = [
    for f in var.batch_task_definition_families :
    "arn:aws:ecs:${var.region}:${local.account_id}:task-definition/${f}:*"
  ]
}

resource "aws_iam_role_policy" "task" {
  name = "${var.name}-task"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid       = "RunLaneTasks"
        Effect    = "Allow"
        Action    = ["ecs:RunTask"]
        Resource  = local.batch_taskdef_arns
        Condition = { ArnEquals = { "ecs:cluster" = var.batch_cluster_arn } }
      },
      {
        # EdgeStep: 제출 전 startedBy 조회·종료 판정. StopTask 는 부르지 않지만(on_kill 무력화) provider
        # 경로에서 권한 오류가 판정을 가리지 않도록 같은 클러스터로 한정해 준다.
        Sid       = "TrackLaneTasks"
        Effect    = "Allow"
        Action    = ["ecs:ListTasks", "ecs:DescribeTasks", "ecs:StopTask"]
        Resource  = ["*"]
        Condition = { ArnEquals = { "ecs:cluster" = var.batch_cluster_arn } }
      },
      {
        Sid      = "DescribeTaskDefinitions"
        Effect   = "Allow"
        Action   = ["ecs:DescribeTaskDefinition"]
        Resource = ["*"]
      },
      {
        Sid       = "PassLaneRoles"
        Effect    = "Allow"
        Action    = ["iam:PassRole"]
        Resource  = var.batch_pass_role_arns
        Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
      },
      {
        Sid      = "NotifyFailure"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = [var.alarm_topic_arn]
      },
      {
        Sid      = "ReadLaneContainerLogs"
        Effect   = "Allow"
        Action   = ["logs:GetLogEvents", "logs:FilterLogEvents", "logs:DescribeLogStreams"]
        Resource = ["${var.batch_log_group_arn}:*"]
      },
      {
        Sid    = "AirflowTaskLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams",
        "logs:FilterLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.tasks.arn}:*"]
      },
      ], flatten([for _ in aws_iam_role.verify_execution : [
        {
          # 격리 검증 DAG — 검증 태스크 정의만, 검증 전용 클러스터(이 클러스터)에서만.
          Sid       = "RunVerifyTasks"
          Effect    = "Allow"
          Action    = ["ecs:RunTask"]
          Resource  = [for k in local.verify_taskdef_keys : "arn:aws:ecs:${var.region}:${local.account_id}:task-definition/${var.name}-verify-${k}:*"]
          Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn } }
        },
        {
          Sid       = "TrackVerifyTasks"
          Effect    = "Allow"
          Action    = ["ecs:ListTasks", "ecs:DescribeTasks", "ecs:StopTask"]
          Resource  = ["*"]
          Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn } }
        },
        {
          Sid       = "PassVerifyRoles"
          Effect    = "Allow"
          Action    = ["iam:PassRole"]
          Resource  = [aws_iam_role.verify_execution[0].arn, aws_iam_role.verify_task[0].arn]
          Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
        },
        {
          Sid      = "ReadVerifyContainerLogs"
          Effect   = "Allow"
          Action   = ["logs:GetLogEvents", "logs:FilterLogEvents", "logs:DescribeLogStreams"]
          Resource = ["${aws_cloudwatch_log_group.verify[0].arn}:*"]
        },
    ]]))
  })
}

# ── 태스크 정의 ─────────────────────────────────────────
locals {
  airflow_env = merge({
    AIRFLOW__CORE__EXECUTOR                    = "LocalExecutor"
    AIRFLOW__CORE__DAGS_FOLDER                 = "/opt/edge/dags"
    AIRFLOW__CORE__LOAD_EXAMPLES               = "False"
    AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION = "True"
    # 동시 task 상한. 장중 수급은 직렬(max_active_runs=1)이고 검증 DAG 가 따로 돌 수 있어 4.
    AIRFLOW__CORE__PARALLELISM                        = "4"
    AIRFLOW__CORE__EXECUTION_API_SERVER_URL           = "http://localhost:${local.api_port}/execution/"
    AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS          = "admin:admin"
    AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE = "/opt/airflow/simple_auth_manager_passwords.json"
    AIRFLOW__API__WORKERS                             = "1"
    AIRFLOW__API__EXPOSE_CONFIG                       = "False"
    AIRFLOW__DATABASE__SQL_ALCHEMY_POOL_SIZE          = "3"
    AIRFLOW__DATABASE__SQL_ALCHEMY_MAX_OVERFLOW       = "5"
    AIRFLOW__LOGGING__REMOTE_LOGGING                  = "True"
    AIRFLOW__LOGGING__REMOTE_BASE_LOG_FOLDER          = "cloudwatch://${aws_cloudwatch_log_group.tasks.arn}"
    AIRFLOW__LOGGING__REMOTE_LOG_CONN_ID              = "aws_default"
    # 자격증명은 태스크 역할(컨테이너 자격증명 엔드포인트)에서 온다 — 연결에는 리전만.
    AIRFLOW_CONN_AWS_DEFAULT = jsonencode({ conn_type = "aws", extra = { region_name = var.region } })
    # entrypoint.sh 가 아래 넷 + 비밀번호로 연결 문자열 파일을 만들고, Airflow 는 _CMD 로 읽는다(헬스체크처럼
    # entrypoint 를 거치지 않는 exec 프로세스도 같은 연결을 본다).
    AIRFLOW__DATABASE__SQL_ALCHEMY_CONN_CMD = "cat /opt/airflow/sql_alchemy_conn"
    EDGE_AIRFLOW_DB_HOST = var.db_host
    EDGE_AIRFLOW_DB_PORT = tostring(var.db_port)
    EDGE_AIRFLOW_DB_NAME = var.db_name
    EDGE_AIRFLOW_DB_USER = var.db_user
    # EdgeStep(dags/edge_batch.py) 배포 환경값.
    EDGE_ECS_CLUSTER         = var.batch_cluster_arn
    EDGE_ECS_TASKDEF_PREFIX  = var.batch_task_definition_prefix
    EDGE_ECS_SUBNETS         = join(",", var.subnet_ids)
    EDGE_ECS_SECURITY_GROUPS = var.batch_security_group_id
    EDGE_ECS_LOG_GROUP       = var.batch_log_group_name
    EDGE_ALARM_TOPIC_ARN     = var.alarm_topic_arn
    }, [for sg in aws_security_group.verify : {
      # 격리 검증 DAG(edge_investor_intraday_verify) — 이 값이 없으면 그 DAG 는 등록되지 않는다.
      EDGE_VERIFY_CLUSTER         = aws_ecs_cluster.this.arn
      EDGE_VERIFY_TASKDEF_PREFIX  = "${var.name}-verify"
      EDGE_VERIFY_SECURITY_GROUPS = sg.id
      EDGE_VERIFY_LOG_GROUP       = aws_cloudwatch_log_group.verify[0].name
  }]...)

  airflow_secrets = {
    EDGE_AIRFLOW_DB_PASSWORD      = "${var.db_password_secret_arn}:password::"
    AIRFLOW__API_AUTH__JWT_SECRET = "${aws_secretsmanager_secret.airflow.arn}:jwt_secret::"
    AIRFLOW__API__SECRET_KEY      = "${aws_secretsmanager_secret.airflow.arn}:api_secret_key::"
    EDGE_AIRFLOW_ADMIN_PASSWORD   = "${aws_secretsmanager_secret.airflow.arn}:admin_password::"
  }

  container_base = {
    image       = var.image
    essential   = true
    environment = [for k, v in local.airflow_env : { name = k, value = v }]
    secrets     = [for k, v in local.airflow_secrets : { name = k, valueFrom = v }]
  }

  # 구성요소별 자원(MiB·CPU unit). 상한(memory)을 넘으면 그 컨테이너가 OOM 으로 죽고 태스크 전체가 교체된다.
  components = {
    api-server = {
      command = ["api-server", "--port", tostring(local.api_port)]
      cpu     = 512
      memory  = 900
      health  = "curl -fs http://localhost:${local.api_port}/api/v2/monitor/health"
      ports   = [{ containerPort = local.api_port, protocol = "tcp" }]
    }
    # LocalExecutor 의 task 프로세스가 이 컨테이너 안에서 돈다 — 상한에 task 몫(parallelism 만큼)을 넣었다.
    scheduler = {
      command = ["scheduler"]
      cpu     = 1024
      memory  = 1600
      health  = "airflow jobs check --job-type SchedulerJob --local"
      ports   = []
    }
    dag-processor = {
      command = ["dag-processor"]
      cpu     = 256
      memory  = 600
      health  = "airflow jobs check --job-type DagProcessorJob --local"
      ports   = []
    }
  }
}

resource "aws_ecs_task_definition" "airflow" {
  family                   = var.name
  requires_compatibilities = ["EC2"]
  network_mode             = "awsvpc"
  memory                   = local.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    for name, c in local.components : merge(local.container_base, {
      name         = name
      command      = c.command
      cpu          = c.cpu
      memory       = c.memory
      portMappings = c.ports
      healthCheck = {
        command     = ["CMD-SHELL", c.health]
        interval    = 60
        timeout     = 30
        retries     = 3
        startPeriod = 180
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.components.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "airflow"
        }
      }
    })
  ])

  # 소유 분리: TF 는 이 리비전(환경·자원·역할)을 만들고, CD(deploy-airflow)는 최신 리비전을 복사해 이미지만
  # 바꾼 새 리비전을 등록한다. TF 의 환경 변경은 다음 배포(또는 workflow_dispatch 재배포)부터 반영된다.
}

# 메타DB 스키마 적용(airflow db migrate) — 배포 워크플로가 서비스 갱신 **전에** 한 번 돌리는 단일 작업.
# 서비스 컨테이너는 마이그레이션을 하지 않는다(여러 구성요소가 동시에 스키마를 바꾸지 않게).
# Fargate(ARM64): 서비스 태스크가 EC2 메모리를 거의 다 쓰므로 같은 호스트에 자리를 요구하지 않는다.
resource "aws_ecs_task_definition" "migrate" {
  family                   = "${var.name}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([merge(local.container_base, {
    name    = "migrate"
    command = ["db", "migrate"]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.components.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "migrate"
      }
    }
  })])
}

resource "aws_ecs_service" "airflow" {
  name            = var.name
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.airflow.arn
  # 0 으로 만든다: 메타DB 스키마가 적용되기 전에는 구성요소를 띄우지 않는다. deploy-airflow 가 마이그레이션
  # 작업 성공 뒤에 새 리비전과 desired 1 로 올린다(이후 desired 는 CD·운영자 소유 — 정지는 0 으로).
  desired_count = 0

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.host.name
    weight            = 1
  }

  # 한 번에 하나만: 옛 태스크를 멈춘 뒤 새 태스크를 띄운다. scheduler 가 둘 뜨는 순간을 만들지 않고,
  # 배포 중 추가 호스트 용량도 필요 없다(대가: 배포 중 1~3분 Airflow 정지 — 슬롯 밖에 배포한다).
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = false
  }

  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }

  depends_on = [aws_ecs_cluster_capacity_providers.this]
}

# ── 최소 알림 ───────────────────────────────────────────
# 서비스 태스크가 없으면(호스트 사망·연속 기동 실패·배포 실패) ECS 서비스 지표가 끊긴다 → 결측을 위반으로 본다.
# 구성요소 하나가 헬스체크에 떨어지면 ECS 가 태스크를 교체하고, 교체가 10분 안에 안 끝나면 여기서 울린다.
# 업무 실패는 이 알람이 아니라 DAG on_failure_callback(SNS)과 원장이 알린다.
resource "aws_cloudwatch_metric_alarm" "service_down" {
  alarm_name          = "${var.name}-service-down"
  alarm_description   = "Airflow 서비스 태스크가 10분 이상 돌지 않는다. 장중 수급이 Airflow 로 전환된 뒤라면 슬롯이 비고 있다. ① ECS 서비스 이벤트·중지된 태스크의 stoppedReason ② ASG 활동(호스트 교체) ③ /ecs/${var.name} 로그. 복구 불가면 README 롤백(Airflow → SFN)."
  namespace           = "AWS/ECS"
  metric_name         = "CPUUtilization"
  dimensions          = { ClusterName = aws_ecs_cluster.this.name, ServiceName = aws_ecs_service.airflow.name }
  statistic           = "SampleCount"
  period              = 300
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [var.alarm_topic_arn]
  ok_actions          = [var.alarm_topic_arn]
}

# ── 배포 권한(CD) ───────────────────────────────────────
# 기존 배포 역할에 이 환경 몫만 덧붙인다(역할·기존 정책은 그대로).
resource "aws_iam_role_policy" "deploy" {
  name = "${var.name}-deploy"
  role = var.deploy_role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload",
          "ecr:DescribeImages", "ecr:GetDownloadUrlForLayer", "ecr:InitiateLayerUpload", "ecr:PutImage",
        "ecr:UploadLayerPart"]
        Resource = [var.ecr_repository_arn]
      },
      {
        Effect    = "Allow"
        Action    = ["ecs:RunTask"]
        Resource  = ["arn:aws:ecs:${var.region}:${local.account_id}:task-definition/${aws_ecs_task_definition.migrate.family}:*"]
        Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn } }
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:UpdateService", "ecs:DescribeServices"]
        Resource = [aws_ecs_service.airflow.id]
      },
      {
        Effect    = "Allow"
        Action    = ["ecs:DescribeTasks"]
        Resource  = ["*"]
        Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn } }
      },
      {
        Effect    = "Allow"
        Action    = ["iam:PassRole"]
        Resource  = [aws_iam_role.execution.arn, aws_iam_role.task.arn]
        Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
      },
      {
        Effect   = "Allow"
        Action   = ["logs:GetLogEvents", "logs:DescribeLogStreams"]
        Resource = ["${aws_cloudwatch_log_group.components.arn}:*"]
      },
    ]
  })
}
