"""관련성 판정기 모델 묶음 내보내기 — 학습 체크포인트 → `tagging.relevance.RelevanceJudge` 가 읽는 묶음 (ALPHA-1476).

학습은 이 저장소 밖(연구 서버)에서 한다. 체크포인트는 학습 모델의 `state_dict`(키 `enc.*`·`head.*`)다.
이 스크립트는 층 이름을 확인해 fp32 그대로 다시 쓰고(int8 은 판정이 바뀐다 — `tagging/relevance.py`),
학습 때 입력 문구(타입별 유형 한글·당사자 설명)를 `meta.json` 에 싣는다.

    python scripts/export_relevance.py --ckpt epoch1.pt --base klue/roberta-base \\
        --out relevance_fp32 --version relevance-klue-base-ep1-fp32 --threshold 0.05

산출 묶음(형식의 정본은 `tagging/relevance.py` 독스트링)은 레이크 `models/relevance/<version>/` 에 올린다.
version 은 `tagging.backend.RELEVANCE_VERSION` 과 같아야 기동된다. 아래 `TYPES` 문구는 학습 코드와 글자까지
같아야 한다(다르면 판정이 조용히 흔들린다).

torch·transformers 가 필요하다(이미지에는 있고 CI 환경에는 없다).
"""

import argparse
import json
import pathlib
import sys

import torch
from transformers import AutoConfig, AutoModel, AutoTokenizer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))  # export_tagger.py 와 같은 방식
from data_pipeline.tagging.relevance import RelevanceHead  # noqa: E402

# 운영 사건 타입 → [유형 한글, 당사자 설명] — 학습 데이터의 5개 유형(계약·실적·인수·인증·소송)에 맞춘다.
_CONTRACT = ["계약 체결", "계약의 공급자 또는 발주처(고객)"]
_EARNINGS = ["실적 발표", "실적을 발표한 회사"]
_ACQUISITION = ["인수·지분 투자", "인수자·투자자, 인수·투자 대상 회사, 또는 매도자"]
_CERTIFICATION = ["제품 인증·승인", "인증·승인을 받은(또는 거절된) 제품의 회사"]
_LEGAL = ["소송·당국 조사·제재", "원고·피고, 또는 당국의 조사·제재 대상"]
TYPES = {
    "COMPANY.CONTRACT.SIGNING": _CONTRACT,
    "COMPANY.EARNINGS.RESULT_RELEASE": _EARNINGS,
    "COMPANY.M_AND_A.ACQUISITION": _ACQUISITION,
    "COMPANY.INVESTMENT.STAKE_ACQUISITION": _ACQUISITION,
    "COMPANY.PRODUCT.CERTIFICATION": _CERTIFICATION,
    "COMPANY.LEGAL.LAWSUIT": _LEGAL,
    "COMPANY.LEGAL.REGULATORY_ACTION": _LEGAL,
}
MAX_LENGTH = 256  # 학습 max_length


def main() -> None:
    """체크포인트를 읽어 묶음을 쓴다."""
    ap = argparse.ArgumentParser()
    for name in ("--ckpt", "--base", "--out", "--version", "--threshold"):
        ap.add_argument(name, required=True)
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    if not 0.0 < float(args.threshold) < 1.0:
        raise SystemExit(f"--threshold 는 (0, 1) 안이어야 한다: {args.threshold}")

    config = AutoConfig.from_pretrained(args.base)
    model = RelevanceHead(AutoModel.from_config(config))
    state = torch.load(args.ckpt, map_location="cpu", weights_only=True)
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing or unexpected:
        raise SystemExit(f"관련성 모델 층 불일치: missing={missing} unexpected={unexpected}")
    (out / "model").mkdir(parents=True, exist_ok=True)
    config.save_pretrained(out / "model")
    torch.save(model.state_dict(), out / "model" / "weights.pt")
    AutoTokenizer.from_pretrained(args.base).save_pretrained(out / "tokenizer")

    meta = {"version": args.version, "threshold": float(args.threshold), "max_length": MAX_LENGTH,
            "types": TYPES}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out), "types": len(TYPES), "threshold": meta["threshold"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
