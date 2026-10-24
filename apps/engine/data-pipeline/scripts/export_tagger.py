"""로컬 태거 모델 묶음 내보내기 — 학습 산출물 → `tagging.roberta.RobertaTagger` 가 읽는 묶음 (ALPHA-1446).

학습은 이 저장소 밖(연구 서버)에서 한다. 이 스크립트는 그 산출물 두 개를 운영 형식으로 바꾼다:
  --cls  분류기 체크포인트(torch.save dict: state·DC·TY·model·maxlen)
  --arg  QA 추출기 디렉터리(save_pretrained + 토크나이저)
  --cat  학습 때 질의 문구를 만든 카탈로그(타입별 required·optional·identity 역할, ko 이름, role_kinds)

    python scripts/export_tagger.py --cls cls_full_y9.pt --arg arg_full_y9 --cat catalog.json \\
        --out tagger_y9_int8 --version tagging-roberta-y9-int8

산출 묶음(형식의 정본은 `tagging/roberta.py` 독스트링)은 레이크 `models/tagger/<version>/` 에
올린다. version 은 `tagging.extract.ROBERTA_TAGGER_VERSION` 과 같아야 기동된다. 가중치는 nn.Linear 를
동적 int8 로 바꾼 state_dict 라 운영이 weights_only 로 읽는다. 질의 문구는 학습 코드와 글자까지
같아야 한다(다르면 추출 정확도가 조용히 떨어진다).

torch·transformers 가 필요하다(이미지에는 있고 CI 환경에는 없다).
"""

import argparse
import json
import pathlib
import sys

import torch
from torch import nn
from transformers import AutoConfig, AutoModel, AutoModelForQuestionAnswering, AutoTokenizer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))  # eval_tagging.py 와 같은 방식
from data_pipeline.tagging.roberta import Classifier  # noqa: E402


def _roles(catalog: dict, event_type: str) -> list[str]:
    """학습 때 그 타입에 물은 역할 순서 — required → optional → identity, 중복 제거."""
    c = catalog[event_type]
    return list(dict.fromkeys(c["required"] + c["optional"] + c["identity"]))


def _query(catalog: dict, event_type: str, role: str) -> str:
    """학습 때와 같은 질의 문구."""
    kind = catalog[event_type]["role_kinds"].get(role) or "VALUE"
    return f"사건 {event_type} {catalog[event_type]['ko']} | 역할 {role} ({kind})"


def _int8_state(model: nn.Module) -> dict:
    """nn.Linear 를 동적 int8 로 바꾼 state_dict."""
    quantized = torch.ao.quantization.quantize_dynamic(model.eval(), {nn.Linear}, dtype=torch.qint8)
    return quantized.state_dict()


def main() -> None:
    """두 학습 산출물을 읽어 묶음을 쓴다."""
    ap = argparse.ArgumentParser()
    for name in ("--cls", "--arg", "--cat", "--out", "--version"):
        ap.add_argument(name, required=True)
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    catalog = json.loads(pathlib.Path(args.cat).read_text(encoding="utf-8"))

    ck = torch.load(args.cls, map_location="cpu", weights_only=False)  # 학습 산출물(자체 생성)
    config = AutoConfig.from_pretrained(ck["model"])
    classifier = Classifier(AutoModel.from_config(config), len(ck["DC"]), len(ck["TY"]))
    missing, unexpected = classifier.load_state_dict(ck["state"], strict=False)
    # 학습 모델에만 있는 dropout 은 가중치가 없다 — 그 밖의 어긋남은 층 구조가 다르다는 뜻이다.
    if missing or any(not k.startswith("drop") for k in unexpected):
        raise SystemExit(f"분류기 층 불일치: missing={missing} unexpected={unexpected}")
    (out / "classifier").mkdir(parents=True, exist_ok=True)
    config.save_pretrained(out / "classifier")
    torch.save(_int8_state(classifier), out / "classifier" / "weights.pt")

    qa = AutoModelForQuestionAnswering.from_pretrained(args.arg)
    (out / "qa").mkdir(exist_ok=True)
    qa.config.save_pretrained(out / "qa")
    torch.save(_int8_state(qa), out / "qa" / "weights.pt")
    AutoTokenizer.from_pretrained(args.arg).save_pretrained(out / "tokenizer")

    meta = {
        "version": args.version, "doc_classes": ck["DC"], "types": ck["TY"], "max_length": ck["maxlen"],
        "queries": {t: [[r, _query(catalog, t, r)] for r in _roles(catalog, t)]
                    for t in ck["TY"] if t in catalog},
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out), "types": len(meta["queries"]), "doc_classes": ck["DC"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
