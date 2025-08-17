from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from datetime import timedelta
from typing import Optional

from ..models import get_db
from ..auth import (
    authenticate_user,
    create_user,
    create_access_token,
    get_current_active_user,
    update_user_preferences,
    get_user_preferences,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from ..activity_tracker import log_user_activity, get_user_dashboard_data

router = APIRouter(
    prefix="/auth",
    tags=["authentication"],
    responses={404: {"description": "Not found"}},
)

templates = Jinja2Templates(directory="templates")


class UserRegister(BaseModel):
    username: str
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    gender: Optional[str] = None
    country: Optional[str] = None


class UserLogin(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str
    user_info: dict


class AuthResponse(BaseModel):
    data: Token
    message: str = "Success"
    status: str = "success"


class UserPreferencesUpdate(BaseModel):
    style_preference: Optional[str] = None
    color_preferences: Optional[list] = None
    body_type: Optional[str] = None
    occasion_types: Optional[list] = None
    budget_range: Optional[str] = None
    gender: Optional[str] = None
    country: Optional[str] = None


@router.get("/login")
async def login_page(request: Request):
    """Serve the login page"""
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/register")
async def register_page(request: Request):
    """Serve the registration page"""
    return templates.TemplateResponse("register.html", {"request": request})


@router.post("/register", response_model=AuthResponse)
async def register_user(
    user_data: UserRegister, request: Request, db: Session = Depends(get_db)
):
    """Register a new user"""

    try:
        # Create new user
        user = create_user(
            db=db,
            username=user_data.username,
            email=user_data.email,
            password=user_data.password,
            full_name=user_data.full_name,
            gender=user_data.gender,
            country=user_data.country,
        )

        # Log registration activity
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        log_user_activity(
            db=db,
            user=user,
            activity_type="user_registration",
            activity_data={"registration_method": "web_form", "user_agent": user_agent},
            ip_address=client_ip,
            user_agent=user_agent,
        )

        # Create access token
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": str(user.id)}, expires_delta=access_token_expires
        )

        token_data = Token(
            access_token=access_token,
            token_type="bearer",
            user_info={
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "full_name": user.full_name,
            },
        )

        return AuthResponse(
            data=token_data, message="User registered successfully", status="success"
        )

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/login", response_model=AuthResponse)
async def login_user(
    user_credentials: UserLogin, request: Request, db: Session = Depends(get_db)
):
    """Authenticate user and return token"""

    user = authenticate_user(db, user_credentials.username, user_credentials.password)
    if not user:
        # Log failed login attempt
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        # Note: We don't have a user object for failed login, so we can't use log_user_activity
        # In a production app, you might want to create a separate failed_login_attempts table

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Log successful login
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    log_user_activity(
        db=db,
        user=user,
        activity_type="user_login",
        activity_data={"login_method": "web_form", "user_agent": user_agent},
        ip_address=client_ip,
        user_agent=user_agent,
    )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id)}, expires_delta=access_token_expires
    )

    token_data = Token(
        access_token=access_token,
        token_type="bearer",
        user_info={
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
        },
    )

    return AuthResponse(data=token_data, message="Login successful", status="success")


@router.get("/me")
async def read_current_user(
    current_user=Depends(get_current_active_user), db: Session = Depends(get_db)
):
    """Get current user information"""

    # Get user preferences
    preferences = get_user_preferences(current_user)

    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "is_active": current_user.is_active,
        "preferences": preferences,
        "member_since": current_user.created_at.isoformat()
        if current_user.created_at
        else None,
    }


@router.put("/preferences")
async def update_preferences(
    preferences_update: UserPreferencesUpdate,
    current_user=Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Update user preferences"""

    # Update user preferences
    updated_user = update_user_preferences(
        db=db,
        user=current_user,
        style_preference=preferences_update.style_preference or "",
        color_preferences=preferences_update.color_preferences or [],
        body_type=preferences_update.body_type or "",
        occasion_types=preferences_update.occasion_types or [],
        budget_range=preferences_update.budget_range or "",
    )

    # Log preference update activity
    log_user_activity(
        db=db,
        user=current_user,
        activity_type="preferences_update",
        activity_data={
            "updated_fields": {
                k: v for k, v in preferences_update.dict().items() if v is not None
            }
        },
    )

    return {
        "message": "Preferences updated successfully",
        "preferences": get_user_preferences(updated_user),
    }


@router.get("/dashboard")
async def get_dashboard(
    current_user=Depends(get_current_active_user), db: Session = Depends(get_db)
):
    """Get user dashboard data"""

    dashboard_data = get_user_dashboard_data(db, current_user)

    # Log dashboard access
    log_user_activity(
        db=db,
        user=current_user,
        activity_type="dashboard_access",
        activity_data={"access_time": dashboard_data["user_info"].get("member_since")},
    )

    return dashboard_data


@router.post("/logout")
async def logout_user(
    current_user=Depends(get_current_active_user), db: Session = Depends(get_db)
):
    """Logout user"""

    # Log logout activity
    log_user_activity(
        db=db,
        user=current_user,
        activity_type="user_logout",
        activity_data={"logout_method": "api_call"},
    )

    return {"message": "Successfully logged out"}


@router.get("/dashboard-page")
async def dashboard_page(
    request: Request, current_user=Depends(get_current_active_user)
):
    """Serve the dashboard page"""
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "user": current_user}
    )
