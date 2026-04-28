from datetime import datetime, timedelta
import hashlib
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.core.config import settings
from app.core.security import create_token, hash_password, verify_password
from app.models.enums import FeatureFlag, PlanTier, UserRole
from app.models.models import PLAN_DEFAULT_FEATURES, PLAN_TOKEN_BUDGETS, RefreshToken, User, UserFeature, UserProfile


class AuthService:
    def __init__(self, db: Session):
        self.db = db

    def register(
        self,
        email: str,
        password: str,
        phone_number: str | None = None,
        age: int | None = None,
        location: str | None = None,
    ) -> User:
        if self.db.query(User).filter_by(email=email).first():
            raise HTTPException(status_code=400, detail="Email already registered")

        # Determine role and plan
        is_admin = email.lower() == settings.admin_email.lower()
        role = UserRole.admin if is_admin else UserRole.user
        plan = PlanTier.pro if is_admin else PlanTier.free  # admin gets pro by default
        budget = PLAN_TOKEN_BUDGETS[plan]

        user = User(
            email=email,
            password_hash=hash_password(password),
            role=role,
            plan=plan,
            token_budget_monthly=budget,
        )
        self.db.add(user)
        self.db.flush()  # get user.id without committing

        # Grant default feature flags based on plan
        features = PLAN_DEFAULT_FEATURES[plan]
        for feature in features:
            self.db.add(UserFeature(user_id=user.id, feature=feature, enabled=True))

        # Create initial profile with optional registration fields
        self.db.add(UserProfile(
            user_id=user.id,
            phone_number=phone_number,
            age=age,
            location=location,
        ))

        self.db.commit()
        self.db.refresh(user)
        return user

    def login(self, email: str, password: str) -> tuple[str, str]:
        user = self.db.query(User).filter_by(email=email).first()
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if not user.is_active:
            raise HTTPException(status_code=403, detail="Account is disabled")
        return self._issue_tokens(user.id)

    def refresh(self, refresh_token: str) -> tuple[str, str]:
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        token_row = self.db.query(RefreshToken).filter_by(token_hash=token_hash, revoked=False).first()
        if not token_row or token_row.expires_at < datetime.utcnow():
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        token_row.revoked = True
        self.db.commit()
        return self._issue_tokens(token_row.user_id)

    def logout(self, refresh_token: str) -> None:
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        token_row = self.db.query(RefreshToken).filter_by(token_hash=token_hash).first()
        if token_row:
            token_row.revoked = True
            self.db.commit()

    def _issue_tokens(self, user_id: int) -> tuple[str, str]:
        access = create_token(str(user_id), "access", timedelta(minutes=settings.access_token_exp_minutes))
        refresh = create_token(str(user_id), "refresh", timedelta(days=settings.refresh_token_exp_days))
        self.db.add(
            RefreshToken(
                user_id=user_id,
                token_hash=hashlib.sha256(refresh.encode()).hexdigest(),
                expires_at=datetime.utcnow() + timedelta(days=settings.refresh_token_exp_days),
            )
        )
        self.db.commit()
        return access, refresh

