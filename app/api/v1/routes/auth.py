from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.models import User, UserFeature
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse, UserMe
from app.services.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _build_user_me(user: User, db: Session) -> UserMe:
    """Build UserMe response with populated features list."""
    feature_rows = db.query(UserFeature).filter_by(user_id=user.id, enabled=True).all()
    features = [f.feature.value for f in feature_rows]
    return UserMe(
        id=user.id,
        email=user.email,
        role=user.role,
        plan=user.plan,
        subscription_status=user.subscription_status,
        token_budget_monthly=user.token_budget_monthly,
        features=features,
    )


@router.post("/register", response_model=UserMe)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    user = AuthService(db).register(payload.email, payload.password)
    return _build_user_me(user, db)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    access, refresh = AuthService(db).login(payload.email, payload.password)
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    access, refresh = AuthService(db).refresh(payload.refresh_token)
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/logout")
def logout(payload: RefreshRequest, db: Session = Depends(get_db)):
    AuthService(db).logout(payload.refresh_token)
    return {"message": "logged out"}


@router.get("/me", response_model=UserMe)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _build_user_me(user, db)

