"""Environment-backed engine configuration."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    qwen_api_key: str | None = None
    # Backward-compatible alias for existing DashScope .env files.
    dashscope_api_key: str | None = None
    qwen_vl_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_vl_model: str = "qwen3.8-flash"
    qwen_vl_timeout_seconds: float = 180.0
    nfa_vit_space_url: str | None = None
    huggingface_token: str | None = None
    lota_weights_path: str = "models/LOTA/Network_best.pth"
    lota_device: str = "auto"
    trufor_enabled: bool = False
    trufor_repo_path: str = "models/TruFor/TruFor_train_test"
    trufor_checkpoint_path: str = (
        "models/TruFor/TruFor_train_test/pretrained_models/trufor.pth.tar"
    )
    trufor_device: str = "cpu"
    ferretnet_repo_path: str = "models/FerretNet"
    ferretnet_weights_path: str = (
        "models/FerretNet/checkpoints/4cls_ckpt/ferretnet-b-median-3.pth"
    )
    ferretnet_device: str = "auto"
    timeout_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            # Environment variables still work without the optional local .env helper.
            pass

        import os

        return cls(
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
            deepseek_base_url=os.getenv(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            ),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            qwen_api_key=(
                os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or None
            ),
            dashscope_api_key=os.getenv("DASHSCOPE_API_KEY") or None,
            qwen_vl_base_url=os.getenv(
                "QWEN_VL_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            qwen_vl_model=os.getenv("QWEN_MODEL", "qwen3.8-flash"),
            qwen_vl_timeout_seconds=float(
                os.getenv("QWEN_VL_TIMEOUT_SECONDS", "180")
            ),
            timeout_seconds=float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "60")),
            nfa_vit_space_url=os.getenv("NFA_VIT_SPACE_URL") or None,
            huggingface_token=os.getenv("HF_TOKEN") or None,
            lota_weights_path=os.getenv(
                "LOTA_WEIGHTS_PATH", "models/LOTA/Network_best.pth"
            ),
            lota_device=os.getenv("LOTA_DEVICE", "auto").strip().lower(),
            trufor_enabled=os.getenv("TRUFOR_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"},
            trufor_repo_path=os.getenv(
                "TRUFOR_REPO_PATH", "models/TruFor/TruFor_train_test"
            ),
            trufor_checkpoint_path=os.getenv(
                "TRUFOR_CHECKPOINT_PATH",
                "models/TruFor/TruFor_train_test/pretrained_models/trufor.pth.tar",
            ),
            trufor_device=os.getenv("TRUFOR_DEVICE", "cpu").strip().lower(),
            ferretnet_repo_path=os.getenv(
                "FERRETNET_REPO_PATH", "models/FerretNet"
            ),
            ferretnet_weights_path=os.getenv(
                "FERRETNET_WEIGHTS_PATH",
                "models/FerretNet/checkpoints/4cls_ckpt/ferretnet-b-median-3.pth",
            ),
            ferretnet_device=os.getenv("FERRETNET_DEVICE", "auto").strip().lower(),
        )
