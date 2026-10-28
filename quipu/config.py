"""Vault configuration: paths, predicates, promotion gate."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import tomllib

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


def load(root: str | Path, create: bool = False) -> Config:
    root = Path(root).expanduser().resolve()
    cfg = Config(root=root)
    path = cfg.config_path
    if path.exists():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
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
