from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.models.models import (
    UserProfile,
    WorkExperience,
    Education,
    Project,
    Skill,
    Certification,
)


class ProfileService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    def get_or_create_profile(self) -> UserProfile:
        profile = self.db.query(UserProfile).filter_by(user_id=self.user_id).first()
        if not profile:
            profile = UserProfile(user_id=self.user_id)
            self.db.add(profile)
            self.db.commit()
            self.db.refresh(profile)
        return profile

    def update_profile(self, payload: dict) -> UserProfile:
        profile = self.get_or_create_profile()
        for k, v in payload.items():
            setattr(profile, k, v)
        self.db.commit()
        self.db.refresh(profile)
        return profile


def upsert_owned(db: Session, model, user_id: int, item_id: int | None, payload: dict):
    if item_id is None:
        obj = model(user_id=user_id, **payload)
        db.add(obj)
    else:
        obj = db.query(model).filter_by(id=item_id, user_id=user_id).first()
        if not obj:
            raise HTTPException(status_code=404, detail="Not found")
        for k, v in payload.items():
            setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return obj


def list_owned(db: Session, model, user_id: int):
    return db.query(model).filter_by(user_id=user_id).order_by(model.id.desc()).all()


def delete_owned(db: Session, model, user_id: int, item_id: int):
    obj = db.query(model).filter_by(id=item_id, user_id=user_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(obj)
    db.commit()


PROFILE_MODELS = {
    "experiences": WorkExperience,
    "educations": Education,
    "projects": Project,
    "skills": Skill,
    "certifications": Certification,
}
