from functools import lru_cache
from pathlib import Path

import yaml

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


@lru_cache
def load_prompt(name: str) -> dict:
    path = PROMPT_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def render_prompt(name: str, **values: object) -> tuple[str, str]:
    prompt = load_prompt(name)
    system = prompt.get("system") or prompt.get("template") or ""
    user_template = prompt.get("user_template") or "{message}"
    return system, user_template.format(**values)
