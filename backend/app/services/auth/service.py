from datetime import datetime, timedelta
import hashlib
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.core.config import settings
from app.core.security import create_token, hash_password, verify_password
from app.models.models import RefreshToken, User


class AuthService:
    def __init__(self, db: Session):
        self.db = db

    def register(self, email: str, password: str) -> User:
        if self.db.query(User).filter_by(email=email).first():
            raise HTTPException(status_code=400, detail="Email already registered")
        user = User(email=email, password_hash=hash_password(password))
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def login(self, email: str, password: str) -> tuple[str, str]:
        user = self.db.query(User).filter_by(email=email).first()
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid credentials")
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
