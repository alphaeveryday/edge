#!/usr/bin/env python3
"""ECR 수명 주기 정책을 적용하기 전에, 만료 대상에 사용 중 이미지가 있는지 확인한다.

아무것도 지우지 않는다 — ECR 의 미리보기(`start-lifecycle-policy-preview`)만 쓴다.

    terraform plan -out=plan.bin
    terraform show -json plan.bin | python3 ecr_lifecycle_preview.py

plan 에 든 저장소별 정책으로 미리보기를 돌려 만료 대상 digest 를 받고, 아래가 참조하는
이미지와 대조한다. 참조가 이미지 인덱스면 그 자식(실제 이미지)을, 참조가 인덱스의 자식이면
그 인덱스를 함께 본다(인덱스가 지워지면 자식도 뒤따라 지워진다).

- ECS: ACTIVE 태스크 정의 패밀리의 최신 revision, 서비스가 쓰는 태스크 정의, 떠 있는 태스크
- Lambda: 컨테이너 이미지 함수의 모든 버전

겹치는 것이 하나라도 있으면 종료 코드 1 이다. 조회가 일부만 성공했을 때도 통과로 끝내지
않고 멈춘다. 서비스 없이 남은 태스크 정의 패밀리는 `--ignore-family` 로 뺀다.
EC2 compose(데모 박스)처럼 AWS API 로 보이지 않는 소비자는 이 스크립트가 보지 못한다.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

_ECR_URI = re.compile(r"^(\d+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com)/([^:@]+)(?:[:@](.+))?$")
_PREVIEW_POLLS = 120  # × 5초 = 10분. 넘기면 통과로 보지 않고 멈춘다.


def parse_ecr_uri(uri: str) -> tuple[str, str] | None:
    """ECR 이미지 URI 를 (저장소, 태그 또는 digest)로 나눈다. ECR 이 아니면 None."""
    m = _ECR_URI.match(uri or "")
    if not m:
        return None
    return m.group(2), m.group(3) or "latest"


def task_image_uris(task: dict) -> list[str]:
    """떠 있는 태스크가 붙든 이미지. digest 가 아직 없으면(PENDING) 태그 URI 로 대조한다."""
    uris = []
    for container in task.get("containers", []):
        m = _ECR_URI.match(container.get("image") or "")
        if not m:
            continue
        digest = container.get("imageDigest")
        uris.append(f"{m.group(1)}/{m.group(2)}@{digest}" if digest else container["image"])
    return uris


def resolve(images: list[dict], ref: str) -> dict | None:
    """태그나 digest 가 가리키는 이미지 상세를 찾는다. 저장소에 없으면 None."""
    for image in images:
        if image["imageDigest"] == ref or ref in (image.get("imageTags") or []):
            return image
    return None


def with_index_relatives(protected: dict[str, list[str]], index_children: dict[str, list[str]]) -> dict[str, list[str]]:
    """사용 중 digest 에 그 인덱스의 자식과, 그 digest 를 자식으로 둔 인덱스를 더한다."""
    out = {digest: list(owners) for digest, owners in protected.items()}
    for index, children in index_children.items():
        for child in children:
            if index in protected:
                out.setdefault(child, []).extend(f"{o} (인덱스의 자식)" for o in protected[index])
            if child in protected:
                out.setdefault(index, []).extend(f"{o} (자식을 둔 인덱스)" for o in protected[child])
    return out


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
    result = json.loads(done.stdout) if done.stdout.strip() else {}
    if isinstance(result, dict) and result.get("failures"):
        # 일부만 받은 응답으로 대조하면 빠진 참조를 "없음"으로 읽는다.
        raise SystemExit(f"aws {' '.join(args[:2])} 가 일부만 응답했다(다시 실행한다): {result['failures'][:3]}")
    return result


def task_definition_images(name: str) -> list[str]:
    td = aws("ecs", "describe-task-definition", "--task-definition", name)["taskDefinition"]
    return [c["image"] for c in td["containerDefinitions"]]


def in_use(ignore_families: set[str]) -> list[tuple[str, str]]:
    """(참조하는 주체, 이미지 URI) 목록. 주체 이름은 사람이 읽을 보고용이다."""
    refs: list[tuple[str, str]] = []
    for family in aws("ecs", "list-task-definition-families", "--status", "ACTIVE")["families"]:
        if family not in ignore_families:
            refs += [(f"태스크 정의 {family}", image) for image in task_definition_images(family)]
    for cluster in aws("ecs", "list-clusters")["clusterArns"]:
        for service in aws("ecs", "list-services", "--cluster", cluster)["serviceArns"]:
            detail = aws("ecs", "describe-services", "--cluster", cluster, "--services", service)
            images = task_definition_images(detail["services"][0]["taskDefinition"])
            refs += [(f"서비스 {service.rsplit('/', 1)[-1]}", image) for image in images]
        tasks = aws("ecs", "list-tasks", "--cluster", cluster)["taskArns"]
        for start in range(0, len(tasks), 100):
            batch = aws("ecs", "describe-tasks", "--cluster", cluster, "--tasks", *tasks[start : start + 100])
            for task in batch["tasks"]:
                refs += [(f"떠 있는 태스크 {task['group']}", uri) for uri in task_image_uris(task)]
    for function in aws("lambda", "list-functions", "--function-version", "ALL")["Functions"]:
        if function.get("PackageType") != "Image":
            continue
        name, version = function["FunctionName"], function["Version"]
        code = aws("lambda", "get-function", "--function-name", name, "--qualifier", version)["Code"]
        refs += [(f"Lambda {name}:{version}", uri) for uri in (code.get("ImageUri"), code.get("ResolvedImageUri")) if uri]
    return refs


def index_children(repository: str, images: list[dict]) -> dict[str, list[str]]:
    """저장소의 모든 인덱스에 대해 {인덱스 digest: [자식 digest]}."""
    indexes = [
        i["imageDigest"]
        for i in images
        if "index" in i.get("imageManifestMediaType", "") or "manifest.list" in i.get("imageManifestMediaType", "")
    ]
    out: dict[str, list[str]] = {}
    for start in range(0, len(indexes), 100):
        ids = [f"imageDigest={d}" for d in indexes[start : start + 100]]
        for got in aws("ecr", "batch-get-image", "--repository-name", repository, "--image-ids", *ids)["images"]:
            manifest = json.loads(got["imageManifest"])
            out[got["imageId"]["imageDigest"]] = [child["digest"] for child in manifest.get("manifests", [])]
    return out


def preview(repository: str, policy: str) -> set[str]:
    aws("ecr", "start-lifecycle-policy-preview", "--repository-name", repository, "--lifecycle-policy-text", policy)
    for _ in range(_PREVIEW_POLLS):
        time.sleep(5)
        result = aws("ecr", "get-lifecycle-policy-preview", "--repository-name", repository)
        if result["status"] != "IN_PROGRESS":
            break
    if result["status"] != "COMPLETE":
        raise SystemExit(f"{repository}: 미리보기가 끝나지 않았다(상태 {result['status']})")
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
        direct: dict[str, list[str]] = {}
        for owner, uri in refs:
            parsed = parse_ecr_uri(uri)
            if not parsed or parsed[0] != repository:
                continue
            image = resolve(images, parsed[1])
            if image is None:
                print(f"  참고: {owner} 가 가리키는 {repository}:{parsed[1][:20]} 은 이미 저장소에 없다")
                continue
            direct.setdefault(image["imageDigest"], []).append(owner)
        protected = with_index_relatives(direct, index_children(repository, images))
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
    # 지키려는 것: 떠 있는 태스크는 digest 가 아직 없어도(PENDING) 대조에서 빠지지 않는다.
    task = {"containers": [
        {"image": f"{registry}/edge/a:v1", "imageDigest": "sha256:run"},
        {"image": f"{registry}/edge/a:v2"},
        {"image": "public.ecr.aws/x/y:1", "imageDigest": "sha256:ext"},
    ]}
    assert task_image_uris(task) == [f"{registry}/edge/a@sha256:run", f"{registry}/edge/a:v2"]
    images = [
        {"imageDigest": "sha256:old", "imageTags": ["v1"]},
        {"imageDigest": "sha256:new", "imageTags": ["v2", "app-latest"]},
        {"imageDigest": "sha256:child"},
    ]
    assert resolve(images, "app-latest")["imageDigest"] == "sha256:new"
    assert resolve(images, "sha256:child")["imageDigest"] == "sha256:child"
    assert resolve(images, "v9") is None
    # 지키려는 것: 인덱스가 지워지면 자식이 뒤따라 지워지므로, 어느 쪽을 붙들었든 둘 다 본다.
    children = {"sha256:idx": ["sha256:child", "sha256:att"], "sha256:other": ["sha256:z"]}
    by_index = with_index_relatives({"sha256:idx": ["서비스 a"]}, children)
    assert set(by_index) == {"sha256:idx", "sha256:child", "sha256:att"}
    by_child = with_index_relatives({"sha256:child": ["태스크 b"]}, children)
    assert set(by_child) == {"sha256:child", "sha256:idx"}
    # 지키려는 것: 사용 중인 이미지가 만료 대상에 있으면 반드시 잡힌다.
    assert find_hits(by_child, {"sha256:idx", "sha256:x"}) == {"sha256:idx": ["태스크 b (자식을 둔 인덱스)"]}
    assert find_hits(by_child, {"sha256:x"}) == {}
    plan = {"planned_values": {"root_module": {"resources": [
        {"type": "aws_ecr_lifecycle_policy", "values": {"repository": "edge/a", "policy": "{}"}},
        {"type": "aws_ecr_repository", "values": {"name": "edge/a"}},
    ]}}}
    assert policies_from_plan(plan) == {"edge/a": "{}"}
    print("self-test 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
