from __future__ import annotations

from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.kit import ApplicationKit


class KitService:
    def __init__(self, db: Session, user_id: int) -> None:
        self.db = db
        self.user_id = user_id

    def list(self) -> list[ApplicationKit]:
        return (
            self.db.query(ApplicationKit)
            .filter(ApplicationKit.user_id == self.user_id)
            .order_by(desc(ApplicationKit.is_default), ApplicationKit.name.asc())
            .all()
        )

    def get(self, kit_id: int) -> ApplicationKit | None:
        return (
            self.db.query(ApplicationKit)
            .filter(
                ApplicationKit.id == kit_id,
                ApplicationKit.user_id == self.user_id,
            )
            .first()
        )

    def _clear_default(self) -> None:
        self.db.query(ApplicationKit).filter(
            ApplicationKit.user_id == self.user_id,
            ApplicationKit.is_default.is_(True),
        ).update({"is_default": False}, synchronize_session=False)

    def _name_exists(self, name: str, exclude_kit_id: int | None = None) -> bool:
        query = self.db.query(ApplicationKit).filter(
            ApplicationKit.user_id == self.user_id,
            ApplicationKit.name == name,
        )
        if exclude_kit_id is not None:
            query = query.filter(ApplicationKit.id != exclude_kit_id)
        return self.db.query(query.exists()).scalar() or False

    def create(self, payload: dict[str, Any]) -> ApplicationKit:
        name = payload.get("name")
        if name and self._name_exists(name):
            raise ValueError(f"Kit with name '{name}' already exists")

        if payload.get("is_default"):
            self._clear_default()

        kit = ApplicationKit(user_id=self.user_id, **payload)
        self.db.add(kit)
        self.db.commit()
        self.db.refresh(kit)
        return kit

    def update(self, kit_id: int, patch: dict[str, Any]) -> ApplicationKit | None:
        kit = self.get(kit_id)
        if kit is None:
            return None

        new_name = patch.get("name")
        if new_name and self._name_exists(new_name, exclude_kit_id=kit_id):
            raise ValueError(f"Kit with name '{new_name}' already exists")

        if patch.get("is_default"):
            self._clear_default()

        for key, value in patch.items():
            setattr(kit, key, value)

        self.db.add(kit)
        self.db.commit()
        self.db.refresh(kit)
        return kit

    def delete(self, kit_id: int) -> bool:
        kit = self.get(kit_id)
        if kit is None:
            return False

        was_default = bool(kit.is_default)
        self.db.delete(kit)
        self.db.commit()

        if was_default:
            replacement = (
                self.db.query(ApplicationKit)
                .filter(ApplicationKit.user_id == self.user_id)
                .order_by(desc(ApplicationKit.created_at))
                .first()
            )
            if replacement is not None:
                replacement.is_default = True
                self.db.add(replacement)
                self.db.commit()

        return True

    def get_default(self) -> ApplicationKit | None:
        default_kit = (
            self.db.query(ApplicationKit)
            .filter(
                ApplicationKit.user_id == self.user_id,
                ApplicationKit.is_default.is_(True),
            )
            .first()
        )
        if default_kit is not None:
            return default_kit

        return (
            self.db.query(ApplicationKit)
            .filter(ApplicationKit.user_id == self.user_id)
            .order_by(desc(ApplicationKit.created_at))
            .first()
        )
