"""
One-off script: reset the admin user's password.
Run from the backend/ directory:
    python scripts/reset_admin_password.py
"""
import sys
import os

# Make sure app is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.enums import FeatureFlag, PlanTier, UserRole
from app.models.models import User, UserFeature

NEW_PASSWORD = "Admin@123"   # ← change this to whatever you want


def main():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email.ilike(settings.admin_email)).first()

        if not user:
            print(f"[!] No user found with email '{settings.admin_email}'.")
            print("    Creating admin account...")
            user = User(
                email=settings.admin_email,
                password_hash=hash_password(NEW_PASSWORD),
                role=UserRole.admin,
                plan=PlanTier.pro,
                token_budget_monthly=500_000,
            )
            db.add(user)
            db.flush()
        else:
            print(f"[✓] Found user: {user.email} (id={user.id}, current role={user.role})")
            user.password_hash = hash_password(NEW_PASSWORD)
            user.role = UserRole.admin
            user.plan = PlanTier.pro

        # Grant all features
        for feature in FeatureFlag:
            existing = db.query(UserFeature).filter_by(user_id=user.id, feature=feature).first()
            if existing:
                existing.enabled = True
            else:
                db.add(UserFeature(user_id=user.id, feature=feature, enabled=True))

        db.commit()
        db.refresh(user)
        print(f"\n✅ Done!")
        print(f"   Email    : {user.email}")
        print(f"   Password : {NEW_PASSWORD}")
        print(f"   Role     : {user.role}")
        print(f"   Plan     : {user.plan}")
        enabled = [f.feature.value for f in db.query(UserFeature).filter_by(user_id=user.id, enabled=True).all()]
        print(f"   Features : {enabled}")
        print(f"\nYou can now log in at http://localhost:3000/login")
    finally:
        db.close()


if __name__ == "__main__":
    main()
