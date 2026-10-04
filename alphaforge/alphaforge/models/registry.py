"""Model registry: artifacts on disk, metadata in DB, champion/challenger lifecycle."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from alphaforge.config import get_settings
from alphaforge.db import session_scope
from alphaforge.db.models import AuditLog, ModelRecord
from .base import SignalModel, load_model

log = logging.getLogger(__name__)


class ModelRegistry:
    def __init__(self, root: Path | None = None):
        self.root = root or get_settings().models_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def register(
        self,
        model: SignalModel,
        *,
        strategy_key: str,
        timeframe: str,
        symbols: list[str],
        metrics: dict,
        trained_from: datetime | None,
        trained_to: datetime | None,
        notes: str = "",
        status: str = "candidate",
    ) -> ModelRecord:
        model_id = f"{model.model_type}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
        path = self.root / model_id
        model.save(path)
        rec = ModelRecord(
            model_id=model_id,
            strategy_key=strategy_key,
            model_type=model.model_type,
            timeframe=timeframe,
            symbols=symbols,
            feature_set=model.feature_names,
            hyperparams=_jsonable(model.hyperparams),
            metrics=_jsonable(metrics),
            status=status,
            artifact_path=str(path),
            trained_from=trained_from,
            trained_to=trained_to,
            notes=notes,
        )
        with session_scope() as s:
            s.add(rec)
        log.info("registered model %s (%s)", model_id, status)
        return rec

    def get(self, model_id: str) -> ModelRecord | None:
        with session_scope() as s:
            return s.scalar(select(ModelRecord).where(ModelRecord.model_id == model_id))

    def load(self, model_id: str) -> SignalModel:
        rec = self.get(model_id)
        if rec is None:
            raise KeyError(model_id)
        return load_model(Path(rec.artifact_path))

    def list(self, strategy_key: str | None = None, status: str | None = None) -> list[ModelRecord]:
        with session_scope() as s:
            q = select(ModelRecord).order_by(ModelRecord.created_at.desc())
            if strategy_key:
                q = q.where(ModelRecord.strategy_key == strategy_key)
            if status:
                q = q.where(ModelRecord.status == status)
            return list(s.scalars(q))

    def champion(self, strategy_key: str = "default") -> ModelRecord | None:
        with session_scope() as s:
            return s.scalar(
                select(ModelRecord).where(ModelRecord.strategy_key == strategy_key, ModelRecord.status == "champion")
            )

    def set_status(self, model_id: str, status: str, actor: str = "system") -> ModelRecord:
        with session_scope() as s:
            rec = s.scalar(select(ModelRecord).where(ModelRecord.model_id == model_id))
            if rec is None:
                raise KeyError(model_id)
            rec.status = status
            s.add(AuditLog(actor=actor, action=f"model.{status}", detail={"model_id": model_id}))
            return rec

    def promote(self, model_id: str, actor: str = "system") -> ModelRecord:
        """Make `model_id` champion; previous champion becomes `retired` (kept for rollback)."""
        with session_scope() as s:
            rec = s.scalar(select(ModelRecord).where(ModelRecord.model_id == model_id))
            if rec is None:
                raise KeyError(model_id)
            prev = s.scalar(
                select(ModelRecord).where(
                    ModelRecord.strategy_key == rec.strategy_key, ModelRecord.status == "champion"
                )
            )
            if prev is not None and prev.model_id != model_id:
                prev.status = "retired"
            rec.status = "champion"
            rec.promoted_at = datetime.now(timezone.utc)
            s.add(AuditLog(actor=actor, action="model.promote", detail={"model_id": model_id, "previous": prev.model_id if prev else None}))
            return rec

    def rollback(self, strategy_key: str = "default", actor: str = "system") -> ModelRecord | None:
        """Re-promote the most recently retired champion."""
        with session_scope() as s:
            prev = s.scalar(
                select(ModelRecord)
                .where(ModelRecord.strategy_key == strategy_key, ModelRecord.status == "retired", ModelRecord.promoted_at.is_not(None))
                .order_by(ModelRecord.promoted_at.desc())
            )
        if prev is None:
            return None
        return self.promote(prev.model_id, actor=actor)


def _jsonable(d: dict) -> dict:
    import json

    return json.loads(json.dumps(d, default=lambda o: float(o) if hasattr(o, "__float__") else str(o)))
