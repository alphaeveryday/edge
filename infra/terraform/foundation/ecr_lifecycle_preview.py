#!/usr/bin/env python3
"""ECR 수명 주기 정책을 적용하기 전에, 만료 대상에 사용 중 이미지가 있는지 확인한다.

아무것도 지우지 않는다 — ECR 의 미리보기(`start-lifecycle-policy-preview`)만 쓴다.

    terraform plan -out=plan.bin
    terraform show -json plan.bin | python3 ecr_lifecycle_preview.py

plan 에 든 저장소별 정책으로 미리보기를 돌려 만료 대상 digest 를 받고, 아래가 참조하는
이미지와 대조한다. 참조가 이미지 인덱스면 그 자식(실제 이미지)까지 함께 본다.

- ECS: ACTIVE 태스크 정의 패밀리의 최신 revision, 서비스가 쓰는 태스크 정의, 실행 중 태스크의 digest
- Lambda: 컨테이너 이미지 함수의 해석된 digest

겹치는 것이 하나라도 있으면 종료 코드 1 이다. 서비스 없이 남은 태스크 정의 패밀리는
`--ignore-family` 로 뺀다. EC2 compose(데모 박스)처럼 AWS API 로 보이지 않는 소비자는
이 스크립트가 보지 못한다.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

_ECR_URI = re.compile(r"^\d+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/([^:@]+)(?:[:@](.+))?$")


def parse_ecr_uri(uri: str) -> tuple[str, str] | None:
    """ECR 이미지 URI 를 (저장소, 태그 또는 digest)로 나눈다. ECR 이 아니면 None."""
    m = _ECR_URI.match(uri or "")
    if not m:
        return None
    return m.group(1), m.group(2) or "latest"


def resolve(images: list[dict], ref: str) -> dict | None:
    """태그나 digest 가 가리키는 이미지 상세를 찾는다. 저장소에 없으면 None."""
    for image in images:
        if image["imageDigest"] == ref or ref in (image.get("imageTags") or []):
            return image
    return None


def find_hits(protected: dict[str, list[str]], expiring: set[str]) -> dict[str, list[str]]:
    """사용 중 digest 가운데 만료 대상에 든 것만 남긴다."""
    return {digest: owners for digest, owners in protected.items() if digest in expiring}


def policies_from_plan(plan: dict) -> dict[str, str]:
    """`terraform show -json` 출력에서 {저장소: 정책 JSON} 을 뽑는다."""
    resources = plan.get("planned_values", {}).get("root_module", {}).get("resources", [])
    return {
        r["values"]["repository"]: r["values"]["policy"]
        for r in resources
        if r["type"] == "aws_ecr_lifecycle_policy"
    }


def aws(*args: str):
    done = subprocess.run(["aws", *args, "--output", "json"], capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"aws {' '.join(args[:2])} 실패: {done.stderr.strip()[:300]}")
    return json.loads(done.stdout) if done.stdout.strip() else {}


def task_definition_images(name: str) -> tuple[str, list[str]]:
    td = aws("ecs", "describe-task-definition", "--task-definition", name)["taskDefinition"]
    return td["family"], [c["image"] for c in td["containerDefinitions"]]


def in_use(ignore_families: set[str]) -> list[tuple[str, str]]:
    """(참조하는 주체, 이미지 URI) 목록. 주체 이름은 사람이 읽을 보고용이다."""
    refs: list[tuple[str, str]] = []
    for family in aws("ecs", "list-task-definition-families", "--status", "ACTIVE")["families"]:
        if family in ignore_families:
            continue
        _, images = task_definition_images(family)
        refs += [(f"태스크 정의 {family}", image) for image in images]
    for cluster in aws("ecs", "list-clusters")["clusterArns"]:
        for service in aws("ecs", "list-services", "--cluster", cluster)["serviceArns"]:
            detail = aws("ecs", "describe-services", "--cluster", cluster, "--services", service)
            _, images = task_definition_images(detail["services"][0]["taskDefinition"])
            refs += [(f"서비스 {service.rsplit('/', 1)[-1]}", image) for image in images]
        tasks = aws("ecs", "list-tasks", "--cluster", cluster)["taskArns"]
        for start in range(0, len(tasks), 100):
            batch = aws("ecs", "describe-tasks", "--cluster", cluster, "--tasks", *tasks[start : start + 100])
            for task in batch["tasks"]:
                for container in task["containers"]:
                    parsed = parse_ecr_uri(container.get("image", ""))
                    if parsed and container.get("imageDigest"):
                        registry = container["image"].split("/", 1)[0]
                        refs.append((f"실행 중 태스크 {task['group']}", f"{registry}/{parsed[0]}@{container['imageDigest']}"))
    for function in aws("lambda", "list-functions")["Functions"]:
        if function.get("PackageType") != "Image":
            continue
        code = aws("lambda", "get-function", "--function-name", function["FunctionName"])["Code"]
        for uri in (code.get("ImageUri"), code.get("ResolvedImageUri")):
            if uri:
                refs.append((f"Lambda {function['FunctionName']}", uri))
    return refs


def index_children(repository: str, digest: str) -> list[str]:
    got = aws("ecr", "batch-get-image", "--repository-name", repository, "--image-ids", f"imageDigest={digest}")
    manifest = json.loads(got["images"][0]["imageManifest"])
    return [child["digest"] for child in manifest.get("manifests", [])]


def preview(repository: str, policy: str) -> set[str]:
    aws("ecr", "start-lifecycle-policy-preview", "--repository-name", repository, "--lifecycle-policy-text", policy)
    while True:
        time.sleep(5)
        result = aws("ecr", "get-lifecycle-policy-preview", "--repository-name", repository)
        if result["status"] != "IN_PROGRESS":
            break
    if result["status"] != "COMPLETE":
        raise SystemExit(f"{repository}: 미리보기 상태 {result['status']}")
    return {image["imageDigest"] for image in result["previewResults"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ignore-family", action="append", default=[], help="대조에서 뺄 태스크 정의 패밀리(반복 가능)")
    parser.add_argument("--self-test", action="store_true", help="AWS 호출 없이 대조 로직만 확인한다")
    args = parser.parse_args()
    if args.self_test:
        return self_test()

    policies = policies_from_plan(json.load(sys.stdin))
    if not policies:
        raise SystemExit("plan 에 aws_ecr_lifecycle_policy 가 없다 — `terraform show -json plan.bin` 출력을 넘겼는지 확인한다")
    refs = in_use(set(args.ignore_family))

    failed = False
    for repository, policy in sorted(policies.items()):
        images = aws("ecr", "describe-images", "--repository-name", repository)["imageDetails"]
        protected: dict[str, list[str]] = {}
        for owner, uri in refs:
            parsed = parse_ecr_uri(uri)
            if not parsed or parsed[0] != repository:
                continue
            image = resolve(images, parsed[1])
            if image is None:
                print(f"  참고: {owner} 가 가리키는 {repository}:{parsed[1][:20]} 은 이미 저장소에 없다")
                continue
            protected.setdefault(image["imageDigest"], []).append(owner)
            if "index" in image.get("imageManifestMediaType", "") or "manifest.list" in image.get("imageManifestMediaType", ""):
                for child in index_children(repository, image["imageDigest"]):
                    protected.setdefault(child, []).append(f"{owner} (인덱스의 자식)")
        expiring = preview(repository, policy)
        hits = find_hits(protected, expiring)
        print(f"{repository}: 이미지 {len(images)}개 중 만료 대상 {len(expiring)}개, 사용 중 {len(protected)}개 중 만료 대상 {len(hits)}개")
        for digest, owners in hits.items():
            failed = True
            print(f"  ✗ {digest[:19]} ← {', '.join(sorted(set(owners)))}")
    print("사용 중 이미지가 만료 대상에 있다 — apply 하지 않는다" if failed else "사용 중 이미지 침범 없음")
    return 1 if failed else 0


def self_test() -> int:
    registry = "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com"
    assert parse_ecr_uri(f"{registry}/edge/pipeline:data-pipeline-latest") == ("edge/pipeline", "data-pipeline-latest")
    assert parse_ecr_uri(f"{registry}/edge/pipeline@sha256:aa") == ("edge/pipeline", "sha256:aa")
    assert parse_ecr_uri("public.ecr.aws/docker/library/nginx:1") is None
    images = [
        {"imageDigest": "sha256:old", "imageTags": ["v1"]},
        {"imageDigest": "sha256:new", "imageTags": ["v2", "app-latest"]},
        {"imageDigest": "sha256:child"},
    ]
    assert resolve(images, "app-latest")["imageDigest"] == "sha256:new"
    assert resolve(images, "sha256:child")["imageDigest"] == "sha256:child"
    assert resolve(images, "v9") is None
    # 지키려는 것: 실행 중인 이미지가 만료 대상에 있으면 반드시 잡힌다.
    protected = {"sha256:old": ["서비스 a"], "sha256:child": ["서비스 b (인덱스의 자식)"]}
    assert find_hits(protected, {"sha256:old", "sha256:x"}) == {"sha256:old": ["서비스 a"]}
    assert find_hits(protected, {"sha256:x"}) == {}
    plan = {"planned_values": {"root_module": {"resources": [
        {"type": "aws_ecr_lifecycle_policy", "values": {"repository": "edge/a", "policy": "{}"}},
        {"type": "aws_ecr_repository", "values": {"name": "edge/a"}},
    ]}}}
    assert policies_from_plan(plan) == {"edge/a": "{}"}
    print("self-test 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
