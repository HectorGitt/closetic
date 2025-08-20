from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from decouple import config
import json

from .models import User, get_db

# Security configuration
SECRET_KEY = str(
    config("JWT_SECRET_KEY", default="your-secret-key-change-in-production")
)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = config("ACCESS_TOKEN_EXPIRE_MINUTES", default=30)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 scheme
security = HTTPBearer()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    """Hash a password"""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create a JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> Optional[dict]:
    """Verify and decode a JWT token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    """Get user by username"""
    return db.query(User).filter(User.username == username).first()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Get user by email"""
    return db.query(User).filter(User.email == email).first()


def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    """Authenticate a user"""
    user = get_user_by_username(db, username)
    if not user:
        return None
    if not verify_password(password, str(user.hashed_password)):
        return None
    return user


def create_user(
    db: Session,
    username: str,
    email: str,
    password: str,
    full_name: Optional[str] = None,
    gender: Optional[str] = None,
    country: Optional[str] = None,
) -> User:
    """Create a new user"""

    # Check if user already exists
    if get_user_by_username(db, username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered",
        )

    if get_user_by_email(db, email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    hashed_password = hash_password(password)

    db_user = User(
        username=username,
        email=email,
        full_name=full_name or "",
        hashed_password=hashed_password,
        is_active=True,
        gender=gender,
        country=country,
    )

    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """Get current user from Bearer token"""

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(
            credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM]
        )
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # Get user by ID since JWT token contains user ID, not username
    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        raise credentials_exception
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Get current active user"""
    if not bool(current_user.is_active):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user"
        )
    return current_user


def update_user_preferences(
    db: Session,
    user: User,
    style_preference: list,
    color_preferences: list,
    body_type: str,
    occasion_types: list,
    budget_range: str,
    gender: str = None,
    country: str = None,
) -> User:
    """Update user preferences"""

    # Update user attributes using setattr to handle SQLAlchemy columns
    setattr(user, "style_preference", json.dumps(style_preference))
    setattr(user, "color_preferences", json.dumps(color_preferences))
    setattr(user, "body_type", body_type)
    setattr(user, "occasion_types", json.dumps(occasion_types))
    setattr(user, "budget_range", budget_range)

    if gender is not None:
        setattr(user, "gender", gender)
    if country is not None:
        setattr(user, "country", country)

    db.commit()
    db.refresh(user)
    return user


def get_user_preferences(user: User, db: Session) -> dict:
    """Get user preferences as a dictionary"""

    style_prefs_str = getattr(user, "style_preference", "") or ""
    color_prefs_str = getattr(user, "color_preferences", "") or ""
    occasion_types_str = getattr(user, "occasion_types", "") or ""

    style_preferences = json.loads(style_prefs_str) if style_prefs_str else []
    color_preferences = json.loads(color_prefs_str) if color_prefs_str else []
    occasion_types = json.loads(occasion_types_str) if occasion_types_str else []
    from app.models import PersonalStyleGuide

    style_guide = (
        db.query(PersonalStyleGuide)
        .filter(PersonalStyleGuide.user_id == user.id)
        .first()
    )
    try:
        style_principles = (
            json.loads(style_guide.style_principles)
            if style_guide.style_principles
            else []
        )
        color_palette = (
            json.loads(style_guide.color_palette) if style_guide.color_palette else []
        )
        essential_pieces = (
            json.loads(style_guide.essential_pieces)
            if style_guide.essential_pieces
            else []
        )
        shopping_priorities = (
            json.loads(style_guide.shopping_priorities)
            if style_guide.shopping_priorities
            else []
        )
        styling_tips = (
            json.loads(style_guide.styling_tips) if style_guide.styling_tips else []
        )
        preferences_snapshot = (
            json.loads(style_guide.preferences_snapshot)
            if style_guide.preferences_snapshot
            else {}
        )
    except (json.JSONDecodeError, TypeError):
        # Fallback for invalid JSON
        style_principles = []
        color_palette = []
        essential_pieces = []
        shopping_priorities = []
        styling_tips = []
        preferences_snapshot = {}
    personal_style_guide = {
        "style_principles": style_principles,
        "color_palette": color_palette,
        "essential_pieces": essential_pieces,
        "shopping_priorities": shopping_priorities,
        "styling_tips": styling_tips,
        "preferences_snapshot": preferences_snapshot,
    }

    return {
        "style_preference": style_preferences,
        "color_preferences": color_preferences,
        "body_type": getattr(user, "body_type", "") or "",
        "occasion_types": occasion_types,
        "budget_range": getattr(user, "budget_range", "") or "",
        "gender": getattr(user, "gender", "") or "",
        "country": getattr(user, "country", "") or "",
        "personal_style_guide": personal_style_guide,
    }
