"""Create/update the isolated NFA-ViT Hugging Face Docker Space."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_id", help="Hugging Face owner/name for the Space")
    parser.add_argument(
        "--public",
        action="store_true",
        help="Make the Space public. Default is private.",
    )
    args = parser.parse_args()

    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise SystemExit(
            "Install huggingface_hub first: python -m pip install huggingface_hub"
        ) from exc

    space_dir = Path(__file__).resolve().parents[1] / "deploy" / "nfa_vit_space"
    api = HfApi()
    api.create_repo(
        repo_id=args.repo_id,
        repo_type="space",
        space_sdk="docker",
        private=not args.public,
        exist_ok=True,
    )
    api.update_repo_settings(
        repo_id=args.repo_id,
        repo_type="space",
        private=not args.public,
    )
    api.upload_folder(
        folder_path=str(space_dir),
        repo_id=args.repo_id,
        repo_type="space",
        ignore_patterns=["__pycache__", "*.pyc"],
    )
    print(f"Uploaded Space source: https://huggingface.co/spaces/{args.repo_id}")
    print("The Space will download the NFA-ViT checkpoints during startup.")


if __name__ == "__main__":
    main()
