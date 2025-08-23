"""
Decorators for limiting API usage based on user pricing tiers.
Using UserActivity table to track AI usage efficiently.
"""

from functools import wraps
from fastapi import HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from typing import Callable
from .models import UserActivity


def limit_ai_usage(
    reset_period: str = "daily",  # daily, weekly, monthly
    free_limit: int = 1,
    spotlight_limit: int = 30,
    elite_limit: int = 100,
    icon_limit: int = -1,  # -1 means unlimited
):
    """
    Decorator to limit AI usage based on user pricing tier.

    Usage:
        @limit_ai_usage(reset_period="daily", free_limit=1)
        @router.post("/analyze-personal")
        async def personal_fashion_analysis(...):
            # AI logic here

    To remove limits, simply delete the decorator line:
        # @limit_ai_usage(reset_period="daily", free_limit=1)

    Args:
        reset_period: "daily", "weekly", or "monthly"
        free_limit: Max AI calls for free tier users in reset period
        spotlight_limit: Max AI calls for spotlight tier users
        elite_limit: Max AI calls for elite tier users
        icon_limit: Max AI calls for icon tier users (-1 = unlimited)
    """

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract dependencies from kwargs - FastAPI passes them as named parameters
            db = kwargs.get("db")
            current_user = kwargs.get("current_user")

            # If not found in kwargs, check args (less likely with FastAPI)
            if not db or not current_user:
                # Look for objects that match our expected types
                for i, arg in enumerate(args):
                    if hasattr(arg, "query") and hasattr(
                        arg, "add"
                    ):  # Session-like object
                        db = arg
                    elif hasattr(arg, "id") and hasattr(
                        arg, "pricing_tier"
                    ):  # User-like object
                        current_user = arg

            # Final validation
            if not db or not current_user:
                raise HTTPException(
                    status_code=500,
                    detail="AI usage limiter requires db session and current user. Check function signature.",
                )

            # Get user's pricing tier
            user_tier = getattr(current_user, "pricing_tier", "free")

            # Get the limit for this tier
            tier_limits = {
                "free": free_limit,
                "spotlight": spotlight_limit,
                "elite": elite_limit,
                "icon": icon_limit,
            }

            user_limit = tier_limits.get(user_tier, free_limit)

            # If unlimited (-1), skip checking
            if user_limit == -1:
                return await func(*args, **kwargs)

            # Calculate the time window for checking usage
            now = datetime.now(timezone.utc)
            if reset_period == "daily":
                start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
            elif reset_period == "weekly":
                days_since_monday = now.weekday()
                start_time = (now - timedelta(days=days_since_monday)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            elif reset_period == "monthly":
                start_time = now.replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                )
            else:
                start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)

            # Create activity type for this specific endpoint
            endpoint_name = func.__name__
            activity_type = f"ai_usage_{endpoint_name}"

            # Count existing AI usage in this period
            usage_count = (
                db.query(UserActivity)
                .filter(
                    UserActivity.user_id == current_user.id,
                    UserActivity.activity_type == activity_type,
                    UserActivity.timestamp >= start_time,
                )
                .count()
            )

            # Check if user has exceeded their limit
            if usage_count >= user_limit:
                from .routers.users import get_tier_features

                tier_features = get_tier_features(user_tier)

                # Calculate reset time
                if reset_period == "daily":
                    reset_time = (now + timedelta(days=1)).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )
                elif reset_period == "weekly":
                    days_until_monday = (7 - now.weekday()) % 7
                    if days_until_monday == 0:
                        days_until_monday = 7
                    reset_time = (now + timedelta(days=days_until_monday)).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )
                else:  # monthly
                    if now.month == 12:
                        reset_time = now.replace(
                            year=now.year + 1,
                            month=1,
                            day=1,
                            hour=0,
                            minute=0,
                            second=0,
                            microsecond=0,
                        )
                    else:
                        reset_time = now.replace(
                            month=now.month + 1,
                            day=1,
                            hour=0,
                            minute=0,
                            second=0,
                            microsecond=0,
                        )

                raise HTTPException(
                    status_code=429,  # Too Many Requests
                    detail={
                        "message": f"{reset_period.title()} AI usage limit reached ({usage_count}/{user_limit} used). Upgrade to {tier_features.get('name', 'higher')} tier for more AI calls.",
                        "upgrade_required": True,
                        "current_usage": usage_count,
                        "limit": user_limit,
                        "reset_time": reset_time.isoformat(),
                        "current_tier": user_tier,
                        "tier_name": tier_features.get("name", user_tier.title()),
                        "reset_period": reset_period,
                        "endpoint": endpoint_name,
                    },
                )

            # Execute the original function
            result = await func(*args, **kwargs)

            # Log the AI usage after successful execution
            from .activity_tracker import log_user_activity

            log_user_activity(
                db=db,
                user=current_user,
                activity_type=activity_type,
                activity_data={
                    "endpoint": endpoint_name,
                    "reset_period": reset_period,
                    "usage_count": usage_count + 1,
                    "limit": user_limit,
                    "tier": user_tier,
                },
            )

            return result

        return wrapper

    return decorator


def check_ai_usage_status(
    user,
    endpoint_name: str,
    db: Session,
    reset_period: str = "daily",
    free_limit: int = 1,
    spotlight_limit: int = 30,
    elite_limit: int = 100,
    icon_limit: int = -1,
) -> dict:
    """
    Check AI usage status for a user without executing the endpoint.
    Useful for frontend to show usage status.

    Returns:
        dict with usage info including current usage, limit, remaining, reset_time
    """
    user_tier = getattr(user, "pricing_tier", "free")

    tier_limits = {
        "free": free_limit,
        "spotlight": spotlight_limit,
        "elite": elite_limit,
        "icon": icon_limit,
    }

    user_limit = tier_limits.get(user_tier, free_limit)

    if user_limit == -1:
        return {
            "unlimited": True,
            "current_usage": 0,
            "limit": -1,
            "remaining": -1,
            "reset_time": None,
            "tier": user_tier,
        }

    # Calculate time window
    now = datetime.now(timezone.utc)
    if reset_period == "daily":
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        reset_time = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif reset_period == "weekly":
        days_since_monday = now.weekday()
        start_time = (now - timedelta(days=days_since_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        days_until_monday = (7 - now.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        reset_time = (now + timedelta(days=days_until_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    else:  # monthly
        start_time = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            reset_time = now.replace(
                year=now.year + 1,
                month=1,
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        else:
            reset_time = now.replace(
                month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0
            )

    # Count usage
    activity_type = f"ai_usage_{endpoint_name}"
    usage_count = (
        db.query(UserActivity)
        .filter(
            UserActivity.user_id == user.id,
            UserActivity.activity_type == activity_type,
            UserActivity.timestamp >= start_time,
        )
        .count()
    )

    return {
        "unlimited": False,
        "current_usage": usage_count,
        "limit": user_limit,
        "remaining": max(0, user_limit - usage_count),
        "reset_time": reset_time.isoformat(),
        "tier": user_tier,
        "allowed": usage_count < user_limit,
    }
