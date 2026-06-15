import json

from sqlalchemy.orm import Session
from fastapi import HTTPException, Request
from app.models.models import (
    KnowledgeDocument,
    UserProfile,
    WorkExperience,
    Education,
    Project,
    Skill,
    Certification,
)
from app.services.audit.service import AuditService
from app.services.analytics.service import ProductEventService


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

    def complete_onboarding(self, payload: dict, request: Request | None = None) -> UserProfile:
        """
        Persist the conversational onboarding answers.

        - Fills `headline` from `current_role` only if not already set.
        - Builds a short career-goals summary and stores it both on the
          profile (`summary`, if empty) and as a `career_goals` knowledge
          document so RAG-backed generation can draw on it.
        - Marks the profile as onboarded, audited and tracked.
        """
        profile = self.get_or_create_profile()

        current_role = (payload.get("current_role") or "").strip()
        career_goals = (payload.get("career_goals") or "").strip()
        target_roles = payload.get("target_roles") or []
        preferred_locations = payload.get("preferred_locations") or []
        salary_expectation = payload.get("salary_expectation")
        deal_breakers = payload.get("deal_breakers") or []

        if current_role and not profile.headline:
            profile.headline = current_role

        summary_lines = [career_goals]
        if target_roles:
            summary_lines.append(f"Target roles: {', '.join(target_roles)}.")
        if preferred_locations:
            summary_lines.append(f"Preferred locations: {', '.join(preferred_locations)}.")
        if salary_expectation:
            summary_lines.append(f"Salary expectation: {salary_expectation}.")
        if deal_breakers:
            summary_lines.append(f"Deal breakers: {', '.join(deal_breakers)}.")
        summary_text = " ".join(line for line in summary_lines if line)

        if not profile.summary:
            profile.summary = career_goals

        profile.current_role = current_role or profile.current_role
        profile.career_goals = career_goals or profile.career_goals
        profile.target_roles = json.dumps(target_roles)
        profile.preferred_locations = json.dumps(preferred_locations)
        profile.salary_expectation = salary_expectation or profile.salary_expectation
        profile.deal_breakers = json.dumps(deal_breakers)
        profile.onboarding_completed = True

        # Replace any prior career-goals knowledge document with the latest answers.
        self.db.query(KnowledgeDocument).filter_by(
            user_id=self.user_id, source_type="career_goals"
        ).delete()
        self.db.add(
            KnowledgeDocument(
                user_id=self.user_id,
                source_type="career_goals",
                source_ref="onboarding",
                content=summary_text,
            )
        )

        self.db.commit()
        self.db.refresh(profile)

        AuditService(self.db).log(
            action="onboarding_completed",
            entity_type="user_profile",
            entity_id=profile.id,
            actor_user_id=self.user_id,
            actor_role="user",
            after={"onboarding_completed": True, "target_roles": target_roles},
            request=request,
        )
        ProductEventService(self.db).track(
            "onboarding_completed",
            user_id=self.user_id,
            properties={"target_roles": target_roles, "preferred_locations": preferred_locations},
            request=request,
        )

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
