from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional, List, Dict, Any
import json

from .models import User, UserActivity, FashionAnalysis


def log_user_activity(
    db: Session,
    user: User,
    activity_type: str,
    activity_data: Dict[Any, Any],
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> UserActivity:
    """Log a user activity to the database"""

    activity = UserActivity(
        user_id=user.id,
        activity_type=activity_type,
        activity_data=json.dumps(activity_data),
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.add(activity)
    db.commit()
    db.refresh(activity)

    return activity


def save_fashion_analysis(
    db: Session,
    user: User,
    analysis_type: str,
    analysis_result: Dict[Any, Any],
    recommendations: Optional[Dict[Any, Any]] = None,
    image_data: Optional[str] = None,
) -> FashionAnalysis:
    """Save a fashion analysis to the database"""

    analysis = FashionAnalysis(
        user_id=user.id,
        analysis_type=analysis_type,
        image_data=image_data,
        analysis_result=json.dumps(analysis_result),
        recommendations=json.dumps(recommendations) if recommendations else None,
    )

    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    return analysis


def get_user_activities(
    db: Session, user: User, activity_type: Optional[str] = None, limit: int = 50
) -> List[UserActivity]:
    """Get user activities, optionally filtered by type"""

    query = db.query(UserActivity).filter(UserActivity.user_id == user.id)

    if activity_type:
        query = query.filter(UserActivity.activity_type == activity_type)

    return query.order_by(UserActivity.timestamp.desc()).limit(limit).all()


def get_user_fashion_analyses(
    db: Session, user: User, analysis_type: Optional[str] = None, limit: int = 20
) -> List[FashionAnalysis]:
    """Get user's fashion analyses"""

    query = db.query(FashionAnalysis).filter(FashionAnalysis.user_id == user.id)

    if analysis_type:
        query = query.filter(FashionAnalysis.analysis_type == analysis_type)

    return query.order_by(FashionAnalysis.created_at.desc()).limit(limit).all()


def get_activity_stats(db: Session, user: User) -> Dict[str, Any]:
    """Get user activity statistics"""

    total_activities = (
        db.query(UserActivity).filter(UserActivity.user_id == user.id).count()
    )
    total_analyses = (
        db.query(FashionAnalysis).filter(FashionAnalysis.user_id == user.id).count()
    )

    # Activity breakdown
    activity_breakdown = (
        db.query(UserActivity.activity_type, func.count(UserActivity.id).label("count"))
        .filter(UserActivity.user_id == user.id)
        .group_by(UserActivity.activity_type)
        .all()
    )

    # Analysis breakdown
    analysis_breakdown = (
        db.query(
            FashionAnalysis.analysis_type,
            func.count(FashionAnalysis.id).label("count"),
        )
        .filter(FashionAnalysis.user_id == user.id)
        .group_by(FashionAnalysis.analysis_type)
        .all()
    )

    return {
        "total_activities": total_activities,
        "total_analyses": total_analyses,
        "activity_breakdown": [
            {"type": item[0], "count": item[1]} for item in activity_breakdown
        ],
        "analysis_breakdown": [
            {"type": item[0], "count": item[1]} for item in analysis_breakdown
        ],
    }


def rate_fashion_analysis(
    db: Session, user: User, analysis_id: int, rating: int
) -> Optional[FashionAnalysis]:
    """Rate a fashion analysis"""

    analysis = (
        db.query(FashionAnalysis)
        .filter(FashionAnalysis.id == analysis_id, FashionAnalysis.user_id == user.id)
        .first()
    )

    if analysis:
        analysis.rating = rating
        db.commit()
        db.refresh(analysis)

    return analysis


def get_user_dashboard_data(db: Session, user: User) -> Dict[str, Any]:
    """Get comprehensive dashboard data for a user"""

    # Recent activities
    recent_activities = get_user_activities(db, user, limit=10)

    # Recent analyses
    recent_analyses = get_user_fashion_analyses(db, user, limit=5)

    # Statistics
    stats = get_activity_stats(db, user)

    # User preferences
    preferences = {
        "style_preference": user.style_preference,
        "color_preferences": json.loads(user.color_preferences)
        if user.color_preferences
        else [],
        "body_type": user.body_type,
        "occasion_types": json.loads(user.occasion_types)
        if user.occasion_types
        else [],
        "budget_range": user.budget_range,
    }

    return {
        "user_info": {
            "username": user.username,
            "full_name": user.full_name,
            "email": user.email,
            "member_since": user.created_at.isoformat() if user.created_at else None,
        },
        "preferences": preferences,
        "statistics": stats,
        "recent_activities": [
            {
                "id": activity.id,
                "type": activity.activity_type,
                "timestamp": activity.timestamp.isoformat()
                if activity.timestamp
                else None,
                "data": json.loads(activity.activity_data)
                if activity.activity_data
                else {},
            }
            for activity in recent_activities
        ],
        "recent_analyses": [
            {
                "id": analysis.id,
                "type": analysis.analysis_type,
                "created_at": analysis.created_at.isoformat()
                if analysis.created_at
                else None,
                "rating": analysis.rating,
                "has_recommendations": bool(analysis.recommendations),
            }
            for analysis in recent_analyses
        ],
    }
