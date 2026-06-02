"""
Provider abstraction for inner voice inference.
Supported: openvino | mlx | cpu | remote.
"""
from abc import ABC, abstractmethod
from pathlib import Path

try:
    import openvino_genai as ov_genai
    _HAS_OPENVINO = True
except ImportError:
    _HAS_OPENVINO = False

try:
    import mlx_lm
    _HAS_MLX = True
except ImportError:
    _HAS_MLX = False

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    _HAS_TRANSFORMERS = True
except ImportError:
    _HAS_TRANSFORMERS = False

_KAARE_BASE = Path("/kaare")
_MODEL_CACHE_DIR = _KAARE_BASE / "services" / "inner_voices" / "ov_cache"
_CPU_THREADS = 12

# Default model identities per provider and voice name
_DEFAULTS: dict[str, dict[str, str]] = {
    "openvino": {
        "jing": str(_KAARE_BASE / "services" / "inner_voices" / "models" / "jing"),
        "jang": str(_KAARE_BASE / "services" / "inner_voices" / "models" / "jang"),
    },
    "mlx": {
        "jing": "mlx-community/Qwen3-0.6B-4bit",
        "jang": "mlx-community/Qwen3-4B-4bit",
    },
    "cpu": {
        "jing": "Qwen/Qwen3-0.6B",
        "jang": "Qwen/Qwen3-4B",
    },
}


class InnerVoiceProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, max_tokens: int) -> str: ...


class OpenVINOProvider(InnerVoiceProvider):
    def __init__(self, model_path: str) -> None:
        if not _HAS_OPENVINO:
            raise RuntimeError(
                "openvino_genai not installed — run: bash services/inner_voices/setup_venv.sh openvino"
            )
        _MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._pipe = ov_genai.LLMPipeline(
            model_path,
            "CPU",
            **{"INFERENCE_NUM_THREADS": _CPU_THREADS, "CACHE_DIR": str(_MODEL_CACHE_DIR)},
        )

    def generate(self, prompt: str, max_tokens: int) -> str:
        cfg = ov_genai.GenerationConfig()
        cfg.max_new_tokens = max_tokens
        return self._pipe.generate(prompt, cfg)


class MLXProvider(InnerVoiceProvider):
    def __init__(self, model_path: str) -> None:
        if not _HAS_MLX:
            raise RuntimeError(
                "mlx_lm not installed — run: bash services/inner_voices/setup_venv.sh mlx"
            )
        self._model, self._tokenizer = mlx_lm.load(model_path)

    def generate(self, prompt: str, max_tokens: int) -> str:
        return mlx_lm.generate(self._model, self._tokenizer, prompt=prompt, max_tokens=max_tokens)


class CPUProvider(InnerVoiceProvider):
    def __init__(self, model_path: str) -> None:
        if not _HAS_TRANSFORMERS:
            raise RuntimeError(
                "transformers/torch not installed — run: bash services/inner_voices/setup_venv.sh cpu"
            )
        self._tokenizer = AutoTokenizer.from_pretrained(model_path)
        self._model = AutoModelForCausalLM.from_pretrained(
            model_path, device_map="cpu", torch_dtype=torch.float32
        )

    def generate(self, prompt: str, max_tokens: int) -> str:
        inputs = self._tokenizer(prompt, return_tensors="pt")
        outputs = self._model.generate(
            **inputs, max_new_tokens=max_tokens, do_sample=False
        )
        new_tokens = outputs[0][inputs["input_ids"].shape[1]:]
        return self._tokenizer.decode(new_tokens, skip_special_tokens=True)


def load_provider(name: str, service_cfg: dict) -> "InnerVoiceProvider | None":
    """
    Return the correct provider for this voice (jing or jang), or None for remote.
    name: "jing" or "jang"
    service_cfg: the jing/jang sub-dict from services.yaml
    """
    prov = service_cfg.get("provider", "openvino")
    if prov == "remote":
        return None

    model_path = (service_cfg.get("model_path") or "").strip()
    if not model_path:
        model_path = _DEFAULTS.get(prov, _DEFAULTS["openvino"]).get(name, "")

    if prov == "openvino":
        return OpenVINOProvider(model_path)
    elif prov == "mlx":
        return MLXProvider(model_path)
    else:
        return CPUProvider(model_path)
