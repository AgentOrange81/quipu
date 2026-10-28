"""Vault configuration: paths, predicates, promotion gate."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.10: keep the stdlib-only promise, parse the subset we write
    tomllib = None

from . import DEFAULT_PREDICATES


@dataclass
class Config:
    root: Path
    predicates: tuple[str, ...] = DEFAULT_PREDICATES
    promote_threshold: float = 0.5
    max_promoted_tokens: int = 4000
    llm_base_url: str = "http://127.0.0.1:8080/v1"
    llm_model: str = "local"
    llm_api_key: str = ""
    extras: dict = field(default_factory=dict)

    @property
    def notes_dir(self) -> Path:
        return self.root / "notes"

    @property
    def entities_dir(self) -> Path:
        return self.root / "entities"

    @property
    def meta_dir(self) -> Path:
        return self.root / ".quipu"

    @property
    def db_path(self) -> Path:
        return self.meta_dir / "index.db"

    @property
    def config_path(self) -> Path:
        return self.meta_dir / "config.toml"

    def ensure(self) -> Config:
        for d in (self.root, self.notes_dir, self.entities_dir, self.meta_dir):
            d.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self.save()
        return self

    def save(self) -> None:
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        preds = ",\n  ".join(f'"{p}"' for p in self.predicates)
        self.config_path.write_text(
            "[quipu]\n"
            f"promote_threshold = {self.promote_threshold}\n"
            f"max_promoted_tokens = {self.max_promoted_tokens}\n"
            f'predicates = [\n  {preds},\n]\n'
            "\n[llm]\n"
            f'base_url = "{self.llm_base_url}"\n'
            f'model = "{self.llm_model}"\n'
            'api_key = ""\n',
            encoding="utf-8",
        )


def _mini_toml(text: str) -> dict:
    """Parse the TOML subset `Config.save()` writes: [sections], `key = value`, string arrays.

    Only used on Python 3.10, where tomllib does not exist. Anything richer is refused loudly rather
    than half-understood — a config the parser fudges is worse than one it rejects.
    """
    import json
    import re

    out: dict = {}
    section: dict = out
    pending: list[str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if pending is not None:
            pending.append(line)
            if "]" in line:
                body = " ".join(pending)
                pending = None
                key, _, value = body.partition("=")
                # TOML allows a trailing comma before `]`; JSON does not
                cleaned = re.sub(r",\s*\]", "]", value.strip()).replace("'", '"')
                section[key.strip()] = json.loads(cleaned)
            continue
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = out.setdefault(line[1:-1].strip(), {})
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and not value.endswith("]"):
            pending = [line]          # multi-line array, as save() emits predicates
            continue
        if value.startswith("["):
            section[key] = json.loads(value.replace("'", '"'))
        elif value.startswith(('"', "'")):
            section[key] = value[1:-1]
        elif value in ("true", "false"):
            section[key] = value == "true"
        else:
            for cast in (int, float):   # 0.5 must parse as a float, not the string "0.5"
                try:
                    section[key] = cast(value)
                    break
                except ValueError:
                    continue
            else:
                section[key] = value
    if pending:
        raise ValueError("unterminated array in config")
    return out


def load(root: str | Path, create: bool = False) -> Config:
    root = Path(root).expanduser().resolve()
    cfg = Config(root=root)
    path = cfg.config_path
    if path.exists():
        text = path.read_text(encoding="utf-8")
        data = tomllib.loads(text) if tomllib else _mini_toml(text)
        q = data.get("quipu", {})
        cfg.predicates = tuple(q.get("predicates", DEFAULT_PREDICATES))
        cfg.promote_threshold = float(q.get("promote_threshold", cfg.promote_threshold))
        cfg.max_promoted_tokens = int(q.get("max_promoted_tokens", cfg.max_promoted_tokens))
        llm = data.get("llm", {})
        cfg.llm_base_url = llm.get("base_url", cfg.llm_base_url)
        cfg.llm_model = llm.get("model", cfg.llm_model)
        cfg.llm_api_key = llm.get("api_key", cfg.llm_api_key)
        cfg.extras = data
    return cfg.ensure() if create else cfg
