from typing import Callable, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.enums import FeatureFlag, UserRole
from app.models.models import User, UserFeature

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def get_current_user(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)) -> User:
    cred_exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "access":
            raise cred_exc
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError):
        raise cred_exc
    user = db.get(User, user_id)
    if not user:
        raise cred_exc
    return user


def get_current_user_optional(
    db: Session = Depends(get_db),
    token: Optional[str] = Depends(oauth2_scheme_optional),
) -> Optional[User]:
    """Returns the current user if authenticated, or None for unauthenticated/ephemeral requests."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "access":
            return None
        user_id = int(payload.get("sub"))
        return db.get(User, user_id)
    except (JWTError, ValueError):
        return None


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Dependency: only admin users can access this endpoint."""
    if user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "admin_required", "message": "This endpoint requires admin access"},
        )
    return user


def require_feature(feature: FeatureFlag) -> Callable:
    """
    Dependency factory: returns a FastAPI dependency that checks whether the
    current user has the given feature enabled.

    Usage:
        @router.get("/kanban", dependencies=[Depends(require_feature(FeatureFlag.kanban))])
    """
    def _check(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        # Admins always have access to everything
        if user.role == UserRole.admin:
            return user
        feature_row = (
            db.query(UserFeature)
            .filter_by(user_id=user.id, feature=feature, enabled=True)
            .first()
        )
        if not feature_row:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "feature_not_enabled",
                    "feature": feature.value,
                    "message": f"Your plan does not include the '{feature.value}' feature. Upgrade to unlock it.",
                },
            )
        return user
    return _check

