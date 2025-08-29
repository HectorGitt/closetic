from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from ..dependencies import get_openai_client
from ..models import (
    get_db,
    User,
    UserActivity,
    FashionAnalysis,
    WardrobeItem,
    OutfitPlan,
)
from ..auth import get_current_active_user

# from ..activity_tracker import get_activity_stats
from pydantic import BaseModel
from typing import List, Optional
import json
from datetime import datetime, timezone
from sqlalchemy import func, desc


def get_admin_user(current_user: User = Depends(get_current_active_user)):
    """Dependency to ensure only admin@closetic.com can access admin endpoints"""
    if current_user.email != "admin@closetic.com":
        raise HTTPException(
            status_code=403, detail="Access denied. Admin privileges required."
        )
    return current_user


router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    responses={404: {"description": "Not found"}},
)

templates = Jinja2Templates(directory="templates")


class AnalyticsData(BaseModel):
    total_analyses: int
    popular_styles: List[str]
    common_issues: List[str]
    user_satisfaction: float


# User Management Models
class UserCreate(BaseModel):
    username: str
    email: str
    full_name: Optional[str] = None
    pricing_tier: str = "free"
    is_active: bool = True
    style_preference: Optional[str] = None
    color_preferences: Optional[str] = None
    body_type: Optional[str] = None
    occasion_types: Optional[str] = None
    budget_range: Optional[str] = None
    gender: Optional[str] = None
    country: Optional[str] = None


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    pricing_tier: Optional[str] = None
    is_active: Optional[bool] = None
    style_preference: Optional[str] = None
    color_preferences: Optional[str] = None
    body_type: Optional[str] = None
    occasion_types: Optional[str] = None
    budget_range: Optional[str] = None
    gender: Optional[str] = None
    country: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str]
    is_active: bool = True
    pricing_tier: str = "free"
    subscription_status: str = "active"
    style_preference: Optional[str]
    color_preferences: Optional[str]
    body_type: Optional[str]
    occasion_types: Optional[str]
    budget_range: Optional[str]
    gender: Optional[str]
    country: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]
    average_fashion_score: float = 0.0
    total_scored_analyses: int = 0


class UserStatusUpdate(BaseModel):
    is_active: bool


class UserListResponse(BaseModel):
    class DataModel(BaseModel):
        users: List[UserResponse]
        total: int
        page: int
        per_page: int

    data: DataModel


class ActivityResponse(BaseModel):
    id: int
    user_id: int
    activity_type: str
    activity_data: Optional[str]
    timestamp: datetime
    ip_address: Optional[str]
    user_agent: Optional[str]
    username: str  # From joined user table


class ActivityListResponse(BaseModel):
    activities: List[ActivityResponse]
    total: int
    page: int
    per_page: int


# In-memory analytics storage (use database in production)
analytics_data = {
    "total_analyses": 0,
    "style_counts": {},
    "issue_counts": {},
    "feedback_scores": [],
}


@router.get("/dashboard")
async def admin_dashboard(
    request: Request,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Serve the admin dashboard"""
    return templates.TemplateResponse(
        "admin_dashboard.html", {"request": request, "user": current_user}
    )


@router.get("/analytics")
async def get_analytics(
    current_user: User = Depends(get_admin_user), db: Session = Depends(get_db)
):
    """Get fashion analysis analytics"""

    # Get overall statistics from database
    # Total analyses count
    total_analyses = db.query(FashionAnalysis).count()

    # Total users count
    # total_users = db.query(User).count()

    # Activity breakdown
    activity_breakdown = (
        db.query(UserActivity.activity_type, func.count(UserActivity.id).label("count"))
        .group_by(UserActivity.activity_type)
        .all()
    )

    # Analysis type breakdown
    analysis_breakdown = (
        db.query(
            FashionAnalysis.analysis_type, func.count(FashionAnalysis.id).label("count")
        )
        .group_by(FashionAnalysis.analysis_type)
        .all()
    )

    analytics = AnalyticsData(
        total_analyses=total_analyses,
        popular_styles=[f"{item[0]}: {item[1]}" for item in analysis_breakdown[:5]],
        common_issues=[f"{item[0]}: {item[1]}" for item in activity_breakdown[:5]],
        user_satisfaction=8.5,  # Default satisfaction score
    )

    return {
        "success": True,
        "data": analytics.model_dump(),
        "message": "Analytics retrieved successfully",
    }


@router.post("/feedback")
async def record_feedback(
    analysis_result: dict,
    user_rating: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Record user feedback for analysis improvement"""

    if not 1 <= user_rating <= 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")

    # Log feedback activity
    from ..activity_tracker import log_user_activity

    log_user_activity(
        db,
        current_user,
        "feedback",
        {
            "rating": user_rating,
            "analysis_type": analysis_result.get("type", "unknown"),
        },
    )

    # Update analytics
    analytics_data["total_analyses"] += 1
    analytics_data["feedback_scores"].append(user_rating)

    # Extract and count style information
    if "style" in analysis_result:
        style = analysis_result["style"]
        analytics_data["style_counts"][style] = (
            analytics_data["style_counts"].get(style, 0) + 1
        )

    # Extract and count common issues
    if "improvements" in analysis_result:
        improvements = analysis_result["improvements"]
        if isinstance(improvements, list):
            for issue in improvements:
                analytics_data["issue_counts"][issue] = (
                    analytics_data["issue_counts"].get(issue, 0) + 1
                )

    return {
        "success": True,
        "data": {
            "rating": user_rating,
            "analysis_type": analysis_result.get("type", "unknown"),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        },
        "message": "Feedback recorded successfully",
    }


@router.get("/trends")
async def get_fashion_trends(
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Get current fashion trends analysis (static demo data)"""

    # Log user activity
    from ..activity_tracker import log_user_activity

    log_user_activity(db, current_user, "trends_view", {})

    # Static/dummy data for demo purposes
    trends_data = {
        "trending_styles": [
            "Minimalist tailoring",
            "Y2K revival",
            "Oversized silhouettes",
            "Utility wear",
            "Sheer layering",
        ],
        "popular_colors": [
            "Sage green & cream",
            "Cobalt blue & white",
            "Pastel pink & grey",
            "Earthy browns",
            "Neon accents",
        ],
        "emerging_movements": [
            "Gender-fluid fashion",
            "Tech-integrated wearables",
            "Upcycled vintage",
            "DIY customization",
        ],
        "sustainability_trends": [
            "Recycled fabrics",
            "Local sourcing",
            "Rental and resale platforms",
            "Zero-waste patterns",
        ],
        "predictions": [
            "More digital fashion/AR try-ons",
            "Return of bold prints",
            "Expansion of circular fashion economy",
        ],
    }

    return {
        "success": True,
        "data": trends_data,
        "message": "Fashion trends retrieved successfully",
    }


@router.get("/style-database")
async def get_style_database(
    current_user: User = Depends(get_admin_user), db: Session = Depends(get_db)
):
    """Get comprehensive style database for reference"""

    # Log user activity
    from ..activity_tracker import log_user_activity

    log_user_activity(db, current_user, "style_database_view", {})

    style_database = {
        "style_categories": {
            "casual": {
                "description": "Relaxed, comfortable, everyday wear",
                "key_pieces": ["jeans", "t-shirts", "sneakers", "hoodies"],
                "occasions": ["daily wear", "shopping", "casual meetups"],
            },
            "formal": {
                "description": "Professional, elegant, structured clothing",
                "key_pieces": ["blazers", "dress shirts", "dress pants", "dress shoes"],
                "occasions": ["work", "business meetings", "formal events"],
            },
            "trendy": {
                "description": "Fashion-forward, current trends, statement pieces",
                "key_pieces": ["statement accessories", "bold patterns", "latest cuts"],
                "occasions": ["parties", "social events", "fashion shows"],
            },
            "classic": {
                "description": "Timeless, elegant, never goes out of style",
                "key_pieces": [
                    "trench coats",
                    "white shirts",
                    "little black dress",
                    "pearls",
                ],
                "occasions": ["any formal occasion", "business", "elegant dinners"],
            },
        },
        "color_theory": {
            "complementary": ["red-green", "blue-orange", "yellow-purple"],
            "analogous": ["blue-blue-green-green", "red-red-orange-orange"],
            "triadic": ["red-yellow-blue", "orange-green-purple"],
            "seasonal_palettes": {
                "spring": ["pastels", "light colors", "fresh greens"],
                "summer": ["cool tones", "blues", "lavenders"],
                "autumn": ["warm earth tones", "oranges", "browns"],
                "winter": ["bold colors", "black", "white", "jewel tones"],
            },
        },
        "body_types": {
            "pear": {
                "characteristics": "Wider hips than shoulders",
                "flattering": ["A-line tops", "boat necks", "wide-leg pants"],
                "avoid": ["tight hips", "tapered pants"],
            },
            "apple": {
                "characteristics": "Fuller midsection",
                "flattering": ["empire waist", "V-necks", "straight cuts"],
                "avoid": ["tight waist", "clingy fabrics"],
            },
            "hourglass": {
                "characteristics": "Balanced shoulders and hips, defined waist",
                "flattering": ["fitted waist", "wrap dresses", "high-waisted bottoms"],
                "avoid": ["boxy cuts", "shapeless clothing"],
            },
            "rectangle": {
                "characteristics": "Similar measurements throughout",
                "flattering": ["belted waists", "layering", "textured fabrics"],
                "avoid": ["straight cuts without definition"],
            },
        },
    }

    return {
        "success": True,
        "data": style_database,
        "message": "Style database retrieved successfully",
    }


@router.get("/user-insights")
async def get_user_insights(
    current_user: User = Depends(get_admin_user), db: Session = Depends(get_db)
):
    """Get insights about user behavior and preferences"""

    # Log user activity
    from ..activity_tracker import log_user_activity

    log_user_activity(db, current_user, "user_insights_view", {})

    # Get insights from database
    # Get top analysis types
    top_analysis_types = (
        db.query(
            FashionAnalysis.analysis_type, func.count(FashionAnalysis.id).label("count")
        )
        .group_by(FashionAnalysis.analysis_type)
        .order_by(func.count(FashionAnalysis.id).desc())
        .limit(5)
        .all()
    )

    # Get top activity types
    top_activity_types = (
        db.query(UserActivity.activity_type, func.count(UserActivity.id).label("count"))
        .group_by(UserActivity.activity_type)
        .order_by(func.count(UserActivity.id).desc())
        .limit(5)
        .all()
    )

    insights = {
        "most_analyzed_styles": [
            f"{item[0]} ({item[1]})" for item in top_analysis_types
        ],
        "common_fashion_issues": [
            f"{item[0]} ({item[1]})" for item in top_activity_types
        ],
        "user_satisfaction_trend": analytics_data["feedback_scores"][-10:]
        if len(analytics_data["feedback_scores"]) >= 10
        else analytics_data["feedback_scores"],
        "total_users_served": analytics_data["total_analyses"],
        "improvement_areas": [
            "Color coordination"
            if "color" in analytics_data["issue_counts"]
            else "Style matching",
            "Fit guidance"
            if "fit" in analytics_data["issue_counts"]
            else "Accessory suggestions",
        ],
    }

    return {
        "success": True,
        "data": insights,
        "message": "User insights retrieved successfully",
    }


# ===== USER MANAGEMENT ENDPOINTS =====


@router.get("/users", response_model=UserListResponse)
async def list_users(
    page: int = 1,
    per_page: int = 20,
    search: Optional[str] = None,
    pricing_tier: Optional[str] = None,
    is_active: Optional[bool] = None,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """List all users with pagination and filtering"""

    query = db.query(User)

    # Apply filters
    if search:
        query = query.filter(
            (User.username.ilike(f"%{search}%"))
            | (User.email.ilike(f"%{search}%"))
            | (User.full_name.ilike(f"%{search}%"))
        )

    if pricing_tier:
        query = query.filter(User.pricing_tier == pricing_tier)

    if is_active is not None:
        query = query.filter(User.is_active == is_active)

    # Get total count
    total = query.count()

    # Apply pagination
    users = (
        query.order_by(desc(User.created_at))
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    # Convert to response model
    user_responses = []
    for user in users:
        user_responses.append(
            UserResponse(
                id=user.id,
                username=user.username,
                email=user.email,
                full_name=user.full_name,
                is_active=user.is_active,
                pricing_tier=user.pricing_tier,
                subscription_status=user.subscription_status,
                style_preference=user.style_preference,
                color_preferences=user.color_preferences,
                body_type=user.body_type,
                occasion_types=user.occasion_types,
                budget_range=user.budget_range,
                gender=user.gender,
                country=user.country,
                created_at=user.created_at,
                updated_at=user.updated_at,
                average_fashion_score=user.average_fashion_score,
                total_scored_analyses=user.total_scored_analyses,
            )
        )

    return UserListResponse(
        data={
            "users": user_responses,
            "total": total,
            "page": page,
            "per_page": per_page,
        }
    )


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Get specific user details"""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        pricing_tier=user.pricing_tier,
        subscription_status=user.subscription_status,
        style_preference=user.style_preference,
        color_preferences=user.color_preferences,
        body_type=user.body_type,
        occasion_types=user.occasion_types,
        budget_range=user.budget_range,
        gender=user.gender,
        country=user.country,
        created_at=user.created_at,
        updated_at=user.updated_at,
        average_fashion_score=user.average_fashion_score,
        total_scored_analyses=user.total_scored_analyses,
    )


@router.post("/users", response_model=UserResponse)
async def create_user(
    user_data: UserCreate,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Create a new user"""

    # Check if username or email already exists
    existing_user = (
        db.query(User)
        .filter((User.username == user_data.username) | (User.email == user_data.email))
        .first()
    )

    if existing_user:
        raise HTTPException(status_code=400, detail="Username or email already exists")

    # Create new user
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        full_name=user_data.full_name,
        pricing_tier=user_data.pricing_tier,
        is_active=user_data.is_active,
        style_preference=user_data.style_preference,
        color_preferences=user_data.color_preferences,
        body_type=user_data.body_type,
        occasion_types=user_data.occasion_types,
        budget_range=user_data.budget_range,
        gender=user_data.gender,
        country=user_data.country,
        hashed_password="placeholder",  # In production, generate proper hash
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return UserResponse(
        id=new_user.id,
        username=new_user.username,
        email=new_user.email,
        full_name=new_user.full_name,
        is_active=new_user.is_active,
        pricing_tier=new_user.pricing_tier,
        subscription_status=new_user.subscription_status,
        style_preference=new_user.style_preference,
        color_preferences=new_user.color_preferences,
        body_type=new_user.body_type,
        occasion_types=new_user.occasion_types,
        budget_range=new_user.budget_range,
        gender=new_user.gender,
        country=new_user.country,
        created_at=new_user.created_at,
        updated_at=new_user.updated_at,
        average_fashion_score=new_user.average_fashion_score,
        total_scored_analyses=new_user.total_scored_analyses,
    )


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Update user information"""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Update fields if provided
    update_data = user_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    user.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(user)

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        pricing_tier=user.pricing_tier,
        subscription_status=user.subscription_status,
        style_preference=user.style_preference,
        color_preferences=user.color_preferences,
        body_type=user.body_type,
        occasion_types=user.occasion_types,
        budget_range=user.budget_range,
        gender=user.gender,
        country=user.country,
        created_at=user.created_at,
        updated_at=user.updated_at,
        average_fashion_score=user.average_fashion_score,
        total_scored_analyses=user.total_scored_analyses,
    )


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Delete a user"""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Soft delete by deactivating instead of hard delete
    user.is_active = False
    user.updated_at = datetime.now(timezone.utc)

    db.commit()

    return {
        "success": True,
        "data": {
            "user_id": user_id,
            "username": user.username,
            "deactivated_at": user.updated_at.isoformat(),
        },
        "message": f"User {user.username} has been deactivated",
    }


@router.patch("/users/{user_id}/status")
async def update_user_status(
    user_id: int,
    status_update: UserStatusUpdate,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Update user active status"""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = status_update.is_active
    user.updated_at = datetime.now(timezone.utc)

    db.commit()

    status_text = "activated" if status_update.is_active else "deactivated"
    return {
        "success": True,
        "data": {
            "user_id": user_id,
            "username": user.username,
            "is_active": status_update.is_active,
            "updated_at": user.updated_at.isoformat(),
        },
        "message": f"User {user.username} has been {status_text}",
    }


# ===== USER ACTIVITY ENDPOINTS =====


@router.get("/activities", response_model=ActivityListResponse)
async def list_activities(
    page: int = 1,
    per_page: int = 50,
    user_id: Optional[int] = None,
    activity_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """List all user activities with pagination and filtering"""

    query = db.query(UserActivity, User.username).join(User)

    # Apply filters
    if user_id:
        query = query.filter(UserActivity.user_id == user_id)

    if activity_type:
        query = query.filter(UserActivity.activity_type == activity_type)

    if start_date:
        query = query.filter(UserActivity.timestamp >= start_date)

    if end_date:
        query = query.filter(UserActivity.timestamp <= end_date)

    # Get total count
    total = query.count()

    # Apply pagination
    activities = (
        query.order_by(desc(UserActivity.timestamp))
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    # Convert to response model
    activity_responses = []
    for activity, username in activities:
        activity_responses.append(
            ActivityResponse(
                id=activity.id,
                user_id=activity.user_id,
                activity_type=activity.activity_type,
                activity_data=activity.activity_data,
                timestamp=activity.timestamp,
                ip_address=activity.ip_address,
                user_agent=activity.user_agent,
                username=username,
            )
        )

    return ActivityListResponse(
        activities=activity_responses, total=total, page=page, per_page=per_page
    )


@router.get("/users/{user_id}/activities", response_model=ActivityListResponse)
async def get_user_activities(
    user_id: int,
    page: int = 1,
    per_page: int = 50,
    activity_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Get activities for a specific user"""

    # Check if user exists
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    query = db.query(UserActivity).filter(UserActivity.user_id == user_id)

    # Apply filters
    if activity_type:
        query = query.filter(UserActivity.activity_type == activity_type)

    if start_date:
        query = query.filter(UserActivity.timestamp >= start_date)

    if end_date:
        query = query.filter(UserActivity.timestamp <= end_date)

    # Get total count
    total = query.count()

    # Apply pagination
    activities = (
        query.order_by(desc(UserActivity.timestamp))
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    # Convert to response model
    activity_responses = []
    for activity in activities:
        activity_responses.append(
            ActivityResponse(
                id=activity.id,
                user_id=activity.user_id,
                activity_type=activity.activity_type,
                activity_data=activity.activity_data,
                timestamp=activity.timestamp,
                ip_address=activity.ip_address,
                user_agent=activity.user_agent,
                username=user.username,
            )
        )

    return ActivityListResponse(
        activities=activity_responses, total=total, page=page, per_page=per_page
    )


@router.get("/users/{user_id}/stats")
async def get_user_stats(
    user_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Get statistics for a specific user"""

    # Check if user exists
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get activity counts by type
    activity_counts = (
        db.query(UserActivity.activity_type, func.count(UserActivity.id).label("count"))
        .filter(UserActivity.user_id == user_id)
        .group_by(UserActivity.activity_type)
        .all()
    )

    # Get fashion analysis count
    analysis_count = (
        db.query(FashionAnalysis).filter(FashionAnalysis.user_id == user_id).count()
    )

    # Get wardrobe item count
    wardrobe_count = (
        db.query(WardrobeItem).filter(WardrobeItem.user_id == user_id).count()
    )

    # Get outfit plan count
    outfit_count = db.query(OutfitPlan).filter(OutfitPlan.user_id == user_id).count()

    # Get recent activities
    recent_activities = (
        db.query(UserActivity)
        .filter(UserActivity.user_id == user_id)
        .order_by(desc(UserActivity.timestamp))
        .limit(10)
        .all()
    )

    user_stats = {
        "user_id": user_id,
        "username": user.username,
        "total_activities": sum(count for _, count in activity_counts),
        "activity_breakdown": {
            activity_type: count for activity_type, count in activity_counts
        },
        "fashion_analyses": analysis_count,
        "wardrobe_items": wardrobe_count,
        "outfit_plans": outfit_count,
        "recent_activities": [
            {
                "activity_type": activity.activity_type,
                "timestamp": activity.timestamp,
                "data": activity.activity_data,
            }
            for activity in recent_activities
        ],
    }

    return {
        "success": True,
        "data": user_stats,
        "message": f"User statistics retrieved successfully for {user.username}",
    }
