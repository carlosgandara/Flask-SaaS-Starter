# /auth/db.py

import os
import datetime
import hashlib

import bcrypt
from dotenv import load_dotenv
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, joinedload


load_dotenv()


# ================================================================
# DATABASE CONFIGURATION
# ================================================================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./test.db"
    print("DATABASE_URL not found. Using SQLite for development.")


# ================================================================
# SQLALCHEMY SETUP
# ================================================================

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


# ================================================================
# MODELS
# ================================================================

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)

    # Email verification
    verified = Column(Boolean, default=False)
    verification_token = Column(String, nullable=True)
    verification_expiry = Column(DateTime, nullable=True)

    # Password reset
    reset_token = Column(String, nullable=True)
    reset_expiry = Column(DateTime, nullable=True)

    # Security and lockout
    failed_login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)

    refresh_tokens = relationship(
        "RefreshToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<User(id={self.id}, email={self.email})>"


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )

    token_hash_sha256 = Column(String(64), nullable=False)
    token_hash_bcrypt = Column(String, nullable=False)

    expires_at = Column(DateTime, nullable=False)
    created_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
    )
    revoked_at = Column(DateTime, nullable=True)

    user = relationship(
        "User",
        back_populates="refresh_tokens",
    )

    __table_args__ = (
        Index(
            "ix_refresh_tokens_token_hash_sha256",
            "token_hash_sha256",
        ),
    )

    def __repr__(self):
        return (
            f"<RefreshToken(id={self.id}, "
            f"user_id={self.user_id}, "
            f"revoked={self.revoked_at is not None})>"
        )


# Create missing tables without deleting existing data.
Base.metadata.create_all(bind=engine)


# ================================================================
# USER DATABASE FUNCTIONS
# ================================================================

def get_all_users():
    db = SessionLocal()

    try:
        return db.query(User).all()
    finally:
        db.close()


def find_user_by_email(email):
    db = SessionLocal()

    try:
        return (
            db.query(User)
            .filter(User.email == email)
            .first()
        )
    finally:
        db.close()


def find_user_by_verification_token(token):
    db = SessionLocal()

    try:
        return (
            db.query(User)
            .filter(User.verification_token == token)
            .first()
        )
    finally:
        db.close()


def find_user_by_reset_token(token):
    db = SessionLocal()

    try:
        users = db.query(User).all()

        for user in users:
            if user.reset_token and bcrypt.checkpw(
                token.encode("utf-8"),
                user.reset_token.encode("utf-8"),
            ):
                return user

        return None
    finally:
        db.close()


def create_user(email, password_hash):
    db = SessionLocal()

    try:
        new_user = User(
            email=email,
            password=password_hash,
        )

        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        return new_user
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def update_user(email, updates):
    db = SessionLocal()

    try:
        user = (
            db.query(User)
            .filter(User.email == email)
            .first()
        )

        if not user:
            return None

        for key, value in updates.items():
            if hasattr(user, key):
                setattr(user, key, value)

        db.commit()
        db.refresh(user)

        return user
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ================================================================
# REFRESH TOKEN DATABASE FUNCTIONS
# ================================================================

def create_refresh_token(user_id, raw_token, expires_at):
    sha256_hash = hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()

    bcrypt_hash = bcrypt.hashpw(
        raw_token.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")

    db = SessionLocal()

    try:
        token_record = RefreshToken(
            user_id=user_id,
            token_hash_sha256=sha256_hash,
            token_hash_bcrypt=bcrypt_hash,
            expires_at=expires_at,
        )

        db.add(token_record)
        db.commit()
        db.refresh(token_record)

        return token_record
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def find_refresh_token_by_raw(raw_token):
    """
    Find an active token using its indexed SHA256 hash,
    then verify it with bcrypt.

    Load the associated user before closing the session.
    """
    sha256_hash = hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()

    db = SessionLocal()

    try:
        token_record = (
            db.query(RefreshToken)
            .options(joinedload(RefreshToken.user))
            .filter(
                RefreshToken.token_hash_sha256 == sha256_hash,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > datetime.datetime.utcnow(),
            )
            .first()
        )

        if not token_record:
            return None

        if bcrypt.checkpw(
            raw_token.encode("utf-8"),
            token_record.token_hash_bcrypt.encode("utf-8"),
        ):
            return token_record

        return None
    finally:
        db.close()


def revoke_refresh_token(token_id):
    db = SessionLocal()

    try:
        token_record = (
            db.query(RefreshToken)
            .filter(RefreshToken.id == token_id)
            .first()
        )

        if not token_record:
            return False

        token_record.revoked_at = datetime.datetime.utcnow()
        db.commit()

        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def revoke_all_user_tokens(user_id):
    db = SessionLocal()

    try:
        (
            db.query(RefreshToken)
            .filter(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .update({
                "revoked_at": datetime.datetime.utcnow(),
            })
        )

        db.commit()

        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_active_refresh_tokens(user_id):
    db = SessionLocal()

    try:
        return (
            db.query(RefreshToken)
            .filter(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > datetime.datetime.utcnow(),
            )
            .all()
        )
    finally:
        db.close()


# ================================================================
# EXISTING COMPATIBILITY STUBS
# ================================================================

def _read_db():
    return {"users": []}


def _write_db(data):
    pass