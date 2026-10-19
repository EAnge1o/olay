"""Run the NFA-ViT Gradio adapter locally, using MPS when it is available."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = Path(
    os.getenv("NFA_VIT_SOURCE_DIR", str(ROOT / "models" / "BR-Gen"))
).expanduser()
MODEL_DIR = SOURCE_DIR / "model_zoo" / "nfa_vit"


def main() -> None:
    if not MODEL_DIR.is_dir():
        raise SystemExit(
            "NFA-ViT source is missing. From the project root, run:\n"
            "  mkdir -p models\n"
            "  git clone --depth 1 https://github.com/clpbc/BR-Gen.git models/BR-Gen\n"
            "Or set NFA_VIT_SOURCE_DIR to an existing BR-Gen checkout."
        )

    os.environ.setdefault("NFA_VIT_SOURCE_DIR", str(SOURCE_DIR))
    os.environ.setdefault("NFA_VIT_DEVICE", "auto")
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    sys.path.insert(0, str(ROOT / "deploy" / "nfa_vit_space"))

    try:
        from app import demo
    except ImportError as exc:
        missing_module = getattr(exc, "name", None)
        missing_detail = (
            f" Missing module: {missing_module}." if missing_module else ""
        )
        raise SystemExit(
            "Could not import the local NFA-ViT service."
            f"{missing_detail} Original error: {exc}\n"
            "Install the optional local dependencies in this same Python environment with:\n"
            '  python -m pip install -e ".[nfa_vit_local]"'
        ) from exc

    demo.launch(
        server_name=os.getenv("NFA_VIT_SERVER_NAME", "127.0.0.1"),
        server_port=int(os.getenv("NFA_VIT_SERVER_PORT", "7860")),
    )


if __name__ == "__main__":
    main()
