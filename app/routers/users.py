from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from ..dependencies import get_openai_client
from ..models import get_db, User, PersonalStyleGuide
from ..auth import get_current_active_user
from ..activity_tracker import (
    log_user_activity,
    save_fashion_analysis,
)
from ..decorators import limit_ai_usage
import json

router = APIRouter(
    prefix="/users",
    tags=["users"],
    responses={404: {"description": "Not found"}},
)

templates = Jinja2Templates(directory="templates")


# Pricing tier helper functions
def get_tier_features(tier: str) -> Dict[str, Any]:
    """Get features and limits for each pricing tier"""
    tier_configs = {
        "free": {
            "name": "Free",
            "max_upload_analyze": 1,
            "max_outfit_plans_per_month": 5,
            "max_wardrobe_items": 10,
            "ai_calls_per_day": 1,  # New: AI usage limit
            "calendar_integration": False,
            "ai_styling_advice": False,
            "weather_integration": False,
            "outfit_alternatives": False,
            "monthly_style_reports": False,
            "priority_support": False,
            "price_monthly": 0,
        },
        "spotlight": {
            "name": "Spotlight",
            "max_upload_analyze": 5,
            "max_outfit_plans_per_month": 30,
            "max_wardrobe_items": 30,
            "ai_calls_per_day": 10,  # New: AI usage limit
            "calendar_integration": True,
            "ai_styling_advice": True,
            "weather_integration": False,
            "outfit_alternatives": True,
            "monthly_style_reports": False,
            "priority_support": False,
            "price_monthly": 9.99,
        },
        "elite": {
            "name": "Elite",
            "max_upload_analyze": 100,
            "max_outfit_plans_per_month": 100,
            "max_wardrobe_items": 50,
            "ai_calls_per_day": 50,  # New: AI usage limit
            "calendar_integration": True,
            "ai_styling_advice": True,
            "weather_integration": True,
            "outfit_alternatives": True,
            "monthly_style_reports": True,
            "priority_support": False,
            "price_monthly": 19.99,
        },
        "icon": {
            "name": "Icon",
            "max_upload_analyze": -1,  # Unlimited
            "max_outfit_plans_per_month": -1,  # Unlimited
            "max_wardrobe_items": -1,  # Unlimited
            "ai_calls_per_day": -1,  # New: Unlimited AI usage
            "calendar_integration": True,
            "ai_styling_advice": True,
            "weather_integration": True,
            "outfit_alternatives": True,
            "monthly_style_reports": True,
            "priority_support": True,
            "price_monthly": 39.99,
        },
    }
    return tier_configs.get(tier, tier_configs["free"])


def is_pro_user(user) -> bool:
    """Check if user has a pro (paid) tier"""
    pro_tiers = ["spotlight", "elite", "icon"]
    return user.pricing_tier in pro_tiers and user.subscription_status == "active"


def check_tier_limits(user, action: str, db) -> Dict[str, Any]:
    """Check if user can perform action based on their tier limits"""
    tier_features = get_tier_features(user.pricing_tier)

    if action == "outfit_plan":
        if tier_features["max_outfit_plans_per_month"] == -1:
            return {"allowed": True, "remaining": -1}

        # Count outfit plans for current month
        current_month = datetime.now().month
        current_year = datetime.now().year

        from ..models import OutfitPlan

        monthly_count = (
            db.query(OutfitPlan)
            .filter(
                OutfitPlan.user_id == user.id,
                db.func.extract("month", OutfitPlan.date) == current_month,
                db.func.extract("year", OutfitPlan.date) == current_year,
            )
            .count()
        )

        allowed = monthly_count < tier_features["max_outfit_plans_per_month"]
        remaining = max(0, tier_features["max_outfit_plans_per_month"] - monthly_count)

        return {"allowed": allowed, "remaining": remaining, "used": monthly_count}

    elif action == "wardrobe_item":
        if tier_features["max_wardrobe_items"] == -1:
            return {"allowed": True, "remaining": -1}

        # Count wardrobe items
        from ..models import WardrobeItem

        item_count = (
            db.query(WardrobeItem).filter(WardrobeItem.user_id == user.id).count()
        )

        allowed = item_count < tier_features["max_wardrobe_items"]
        remaining = max(0, tier_features["max_wardrobe_items"] - item_count)

        return {"allowed": allowed, "remaining": remaining, "used": item_count}

    return {"allowed": True, "remaining": -1}


# Structured output models for OpenAI
class PersonalStyleGuideResponse(BaseModel):
    style_principles: List[str]
    color_palette: List[str]
    essential_pieces: List[str]
    shopping_priorities: List[str]
    styling_tips: List[str]


class PersonalizedAnalysis(BaseModel):
    style_match_score: float
    personalized_adjustments: List[str]
    style_alternatives: List[str]
    occasion_adaptations: List[str]


class StyleCompatibility(BaseModel):
    style_score: float
    color_score: float
    occasion_score: float
    overall_compatibility: float
    improvement_suggestions: List[str]


class WardrobePlan(BaseModel):
    essentials: List[str]
    statement_pieces: List[str]
    accessories: List[str]
    seasonal_items: List[str]
    outfit_combinations: List[str]


class UserPreferences(BaseModel):
    style_preference: List[str]  # casual, formal, trendy, classic
    color_preferences: List[str]
    body_type: Optional[str] = None
    occasion_types: List[str]  # work, party, casual, formal
    budget_range: Optional[str] = None
    gender: Optional[str] = None  # male, female, non-binary, prefer-not-to-say
    country: Optional[str] = None  # User's country for cultural context


class UserProfile(BaseModel):
    username: str
    preferences: UserPreferences
    fashion_history: Optional[List[dict]] = []


# Pricing tier models
class PricingTierResponse(BaseModel):
    user_id: int
    pricing_tier: str
    is_pro: bool
    subscription_status: str
    subscription_start_date: Optional[datetime]
    subscription_end_date: Optional[datetime]
    tier_features: Dict[str, Any]


class UpdatePricingTierRequest(BaseModel):
    pricing_tier: str  # free, spotlight, elite, icon
    subscription_months: Optional[int] = None


# Pricing tier endpoints
@router.get("/pricing-tier")
async def get_user_pricing_tier(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Get current user's pricing tier and subscription details"""
    try:
        tier_features = get_tier_features(current_user.pricing_tier)

        return {
            "success": True,
            "data": {
                "user_id": current_user.id,
                "pricing_tier": current_user.pricing_tier,
                "is_pro": is_pro_user(current_user),
                "subscription_status": current_user.subscription_status,
                "subscription_start_date": current_user.subscription_start_date.isoformat()
                if current_user.subscription_start_date
                else None,
                "subscription_end_date": current_user.subscription_end_date.isoformat()
                if current_user.subscription_end_date
                else None,
                "tier_features": tier_features,
            },
            "message": f"Current pricing tier: {tier_features['name']}",
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching pricing tier: {str(e)}"
        )


@router.post("/pricing-tier/upgrade")
async def upgrade_pricing_tier(
    tier_request: UpdatePricingTierRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Upgrade user's pricing tier (for admin or payment processing)"""
    try:
        valid_tiers = ["free", "spotlight", "elite", "icon"]
        if tier_request.pricing_tier not in valid_tiers:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid pricing tier. Must be one of: {', '.join(valid_tiers)}",
            )

        # Update user's pricing tier
        current_user.pricing_tier = tier_request.pricing_tier
        current_user.subscription_status = "active"

        if tier_request.pricing_tier != "free":
            # Set subscription dates for paid tiers
            current_user.subscription_start_date = datetime.now()
            if tier_request.subscription_months:
                current_user.subscription_end_date = datetime.now() + timedelta(
                    days=30 * tier_request.subscription_months
                )
            else:
                # Default to 1 month
                current_user.subscription_end_date = datetime.now() + timedelta(days=30)
        else:
            # Free tier doesn't have subscription dates
            current_user.subscription_start_date = None
            current_user.subscription_end_date = None

        current_user.updated_at = datetime.now()
        db.commit()

        # Log activity
        log_user_activity(
            db=db,
            user=current_user,
            activity_type="pricing_tier_upgraded",
            activity_data={
                "old_tier": "free",  # We could track this better
                "new_tier": tier_request.pricing_tier,
                "subscription_months": tier_request.subscription_months,
            },
        )

        tier_features = get_tier_features(current_user.pricing_tier)

        return {
            "success": True,
            "data": {
                "user_id": current_user.id,
                "pricing_tier": current_user.pricing_tier,
                "is_pro": is_pro_user(current_user),
                "subscription_status": current_user.subscription_status,
                "subscription_start_date": current_user.subscription_start_date.isoformat()
                if current_user.subscription_start_date
                else None,
                "subscription_end_date": current_user.subscription_end_date.isoformat()
                if current_user.subscription_end_date
                else None,
                "tier_features": tier_features,
            },
            "message": f"Successfully upgraded to {tier_features['name']} tier",
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Error upgrading pricing tier: {str(e)}"
        )


@router.get("/pricing-tiers/all")
async def get_all_pricing_tiers():
    """Get all available pricing tiers and their features"""
    try:
        tiers = ["free", "spotlight", "elite", "icon"]
        all_tiers = {}

        for tier in tiers:
            all_tiers[tier] = get_tier_features(tier)

        return {
            "success": True,
            "data": {
                "pricing_tiers": all_tiers,
                "recommended_tier": "elite",  # Marketing recommendation
            },
            "message": "Retrieved all pricing tiers",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching pricing tiers: {str(e)}"
        )


@router.get("/tier-limits/{action}")
async def check_user_tier_limits(
    action: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Check if user can perform a specific action based on their tier limits"""
    try:
        valid_actions = ["outfit_plan", "wardrobe_item"]
        if action not in valid_actions:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid action. Must be one of: {', '.join(valid_actions)}",
            )

        limits = check_tier_limits(current_user, action, db)
        tier_features = get_tier_features(current_user.pricing_tier)

        return {
            "success": True,
            "data": {
                "action": action,
                "pricing_tier": current_user.pricing_tier,
                "tier_name": tier_features["name"],
                "is_pro": is_pro_user(current_user),
                "limits": limits,
                "upgrade_required": not limits["allowed"],
            },
            "message": f"Action '{action}' {'allowed' if limits['allowed'] else 'requires upgrade'}",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error checking tier limits: {str(e)}"
        )


@router.get("/ai-usage/{endpoint_name}")
async def check_ai_usage(
    endpoint_name: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Check AI usage status for a specific endpoint"""
    try:
        # Get tier features to determine limits
        tier_features = get_tier_features(current_user.pricing_tier)
        ai_limit = tier_features.get("ai_calls_per_day", 1)

        # Import the function here to avoid circular import
        from ..decorators import check_ai_usage_status

        usage_status = check_ai_usage_status(
            user=current_user,
            endpoint_name=endpoint_name,
            db=db,
            reset_period="monthly",
            free_limit=1,
            spotlight_limit=10,
            elite_limit=50,
            icon_limit=-1,
        )

        return {
            "success": True,
            "data": {
                "endpoint": endpoint_name,
                "pricing_tier": current_user.pricing_tier,
                "tier_name": tier_features["name"],
                "ai_usage": usage_status,
                "tier_limit": ai_limit,
            },
            "message": f"AI usage status for {endpoint_name}",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error checking AI usage: {str(e)}"
        )


@router.get("/profile")
async def profile_page(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Serve the user profile page"""
    return templates.TemplateResponse(
        "profile.html", {"request": request, "user": current_user}
    )


@router.put("/preferences")
@limit_ai_usage(
    reset_period="monthly",
    free_limit=1,
    spotlight_limit=10,
    elite_limit=30,
    icon_limit=-1,
)
async def update_preferences(
    preferences: UserPreferences,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    client=Depends(get_openai_client),
):
    """Update user fashion preferences"""

    try:
        # Update user preferences in database
        from ..auth import update_user_preferences

        update_user_preferences(
            db=db,
            user=current_user,
            style_preference=preferences.style_preference,
            color_preferences=preferences.color_preferences,
            body_type=preferences.body_type or "",
            occasion_types=preferences.occasion_types,
            budget_range=preferences.budget_range or "",
            gender=preferences.gender,
            country=preferences.country,
        )

        # Generate personalized style guide using OpenAI
        style_guide = await generate_personal_style_guide(client, preferences)

        # Save style guide to database
        # First, deactivate any existing active style guides
        db.query(PersonalStyleGuide).filter(
            PersonalStyleGuide.user_id == current_user.id, PersonalStyleGuide.is_active
        ).update({"is_active": False})

        # Create new style guide
        new_style_guide = PersonalStyleGuide(
            user_id=current_user.id,
            style_principles=json.dumps(style_guide.get("style_principles", [])),
            color_palette=json.dumps(style_guide.get("color_palette", [])),
            essential_pieces=json.dumps(style_guide.get("essential_pieces", [])),
            shopping_priorities=json.dumps(style_guide.get("shopping_priorities", [])),
            styling_tips=json.dumps(style_guide.get("styling_tips", [])),
            preferences_snapshot=json.dumps(
                {
                    "style_preference": preferences.style_preference,
                    "color_preferences": preferences.color_preferences,
                    "body_type": preferences.body_type,
                    "occasion_types": preferences.occasion_types,
                    "budget_range": preferences.budget_range,
                }
            ),
            is_active=True,
        )

        db.add(new_style_guide)
        db.commit()
        db.refresh(new_style_guide)

        # Log activity
        log_user_activity(
            db=db,
            user=current_user,
            activity_type="preferences_updated",
            activity_data={
                "style_preference": preferences.style_preference,
                "color_count": len(preferences.color_preferences),
                "body_type": preferences.body_type,
                "occasion_count": len(preferences.occasion_types),
                "budget_range": preferences.budget_range,
                "style_guide_id": new_style_guide.id,
            },
        )

        return {
            "message": "Preferences updated successfully",
            "data": {
                "personal_style_guide": style_guide,
                "style_guide_id": new_style_guide.id,
                "saved_to_database": True,
                "created_at": new_style_guide.created_at.isoformat(),
            },
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/preferences")
async def get_preferences(
    request: Request,
    username: Optional[str] = None,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Get user preferences (own or by username if admin)"""

    # If username is not provided, use current user's username
    target_username = username or str(current_user.username)

    # Only allow access to own preferences or if admin
    if str(current_user.username) != target_username:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get preferences from database
    from ..auth import get_user_preferences

    preferences = get_user_preferences(current_user, db)

    # Log activity
    log_user_activity(
        db=db,
        user=current_user,
        activity_type="preferences_viewed",
        activity_data={"requested_username": target_username},
    )

    return preferences


@router.get("/style-guide")
async def get_current_style_guide(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Get user's current active personal style guide"""
    try:
        # Get the current active style guide
        style_guide = (
            db.query(PersonalStyleGuide)
            .filter(
                PersonalStyleGuide.user_id == current_user.id,
                PersonalStyleGuide.is_active,
            )
            .first()
        )

        if not style_guide:
            return {
                "success": True,
                "data": {
                    "has_style_guide": False,
                    "message": "No personal style guide found. Update your preferences to generate one.",
                },
                "message": "No active style guide found",
            }

        # Parse JSON fields
        try:
            style_principles = (
                json.loads(style_guide.style_principles)
                if style_guide.style_principles
                else []
            )
            color_palette = (
                json.loads(style_guide.color_palette)
                if style_guide.color_palette
                else []
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

        return {
            "success": True,
            "data": {
                "has_style_guide": True,
                "style_guide_id": style_guide.id,
                "personal_style_guide": {
                    "style_principles": style_principles,
                    "color_palette": color_palette,
                    "essential_pieces": essential_pieces,
                    "shopping_priorities": shopping_priorities,
                    "styling_tips": styling_tips,
                },
                "preferences_snapshot": preferences_snapshot,
                "created_at": style_guide.created_at.isoformat(),
                "updated_at": style_guide.updated_at.isoformat()
                if style_guide.updated_at
                else None,
            },
            "message": "Retrieved current personal style guide",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching style guide: {str(e)}"
        )


@router.get("/style-guide/history")
async def get_style_guide_history(
    limit: int = 10,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Get user's style guide history"""
    try:
        # Get all style guides for the user
        style_guides = (
            db.query(PersonalStyleGuide)
            .filter(PersonalStyleGuide.user_id == current_user.id)
            .order_by(PersonalStyleGuide.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        # Get total count
        total_count = (
            db.query(PersonalStyleGuide)
            .filter(PersonalStyleGuide.user_id == current_user.id)
            .count()
        )

        # Format response
        formatted_guides = []
        for guide in style_guides:
            try:
                preferences_snapshot = (
                    json.loads(guide.preferences_snapshot)
                    if guide.preferences_snapshot
                    else {}
                )
            except (json.JSONDecodeError, TypeError):
                preferences_snapshot = {}

            formatted_guides.append(
                {
                    "id": guide.id,
                    "is_active": guide.is_active,
                    "preferences_snapshot": preferences_snapshot,
                    "created_at": guide.created_at.isoformat(),
                    "updated_at": guide.updated_at.isoformat()
                    if guide.updated_at
                    else None,
                }
            )

        return {
            "success": True,
            "data": {
                "style_guides": formatted_guides,
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + len(formatted_guides)) < total_count,
            },
            "message": f"Retrieved {len(formatted_guides)} style guides",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching style guide history: {str(e)}"
        )


@router.post("/analyze-personal")
@limit_ai_usage(
    reset_period="monthly",
    free_limit=1,
    spotlight_limit=10,
    elite_limit=50,
    icon_limit=-1,
)
async def personal_fashion_analysis(
    image_analysis: dict,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    client=Depends(get_openai_client),
):
    """Provide personalized fashion analysis based on user preferences"""

    try:
        # Get user preferences from database
        from ..auth import get_user_preferences

        user_preferences = get_user_preferences(current_user)

        # Convert to UserPreferences model for compatibility
        preferences_obj = UserPreferences(
            style_preference=user_preferences.get("style_preference", "casual"),
            color_preferences=user_preferences.get("color_preferences", []),
            body_type=user_preferences.get("body_type"),
            occasion_types=user_preferences.get("occasion_types", []),
            budget_range=user_preferences.get("budget_range"),
        )

        # Generate personalized recommendations
        personal_analysis = await generate_personalized_analysis(
            client, image_analysis, preferences_obj
        )

        # Save analysis to database
        save_fashion_analysis(
            db=db,
            user=current_user,
            analysis_type="personal_analysis",
            analysis_result=personal_analysis,
            recommendations=None,
            image_data=None,
        )

        # Log activity
        log_user_activity(
            db=db,
            user=current_user,
            activity_type="personal_analysis",
            activity_data={
                "analysis_provided": True,
                "style_preference": user_preferences.get("style_preference"),
            },
        )

        # Get user's current style guide as reference
        current_style_guide = (
            db.query(PersonalStyleGuide)
            .filter(
                PersonalStyleGuide.user_id == current_user.id,
                PersonalStyleGuide.is_active,
            )
            .first()
        )

        # Format style guide for response
        style_guide_reference = None
        if current_style_guide:
            try:
                style_guide_reference = {
                    "style_principles": json.loads(current_style_guide.style_principles)
                    if current_style_guide.style_principles
                    else [],
                    "color_palette": json.loads(current_style_guide.color_palette)
                    if current_style_guide.color_palette
                    else [],
                    "essential_pieces": json.loads(current_style_guide.essential_pieces)
                    if current_style_guide.essential_pieces
                    else [],
                    "shopping_priorities": json.loads(
                        current_style_guide.shopping_priorities
                    )
                    if current_style_guide.shopping_priorities
                    else [],
                    "styling_tips": json.loads(current_style_guide.styling_tips)
                    if current_style_guide.styling_tips
                    else [],
                    "created_at": current_style_guide.created_at.isoformat()
                    if current_style_guide.created_at
                    else None,
                }
            except (json.JSONDecodeError, TypeError):
                style_guide_reference = {"error": "Unable to parse style guide data"}

        return {
            "personal_analysis": personal_analysis,
            "style_compatibility": await check_style_compatibility(
                client, image_analysis, preferences_obj
            ),
            "personal_style_guide": style_guide_reference,
            "has_style_guide": style_guide_reference is not None,
            "references": {
                "style_guide": style_guide_reference,
                "user_preferences": user_preferences,
                "analysis_date": datetime.now().isoformat(),
            },
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_fashion_history(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Get user's fashion analysis history"""
    try:
        from ..models import FashionAnalysis

        # Query user's fashion analyses
        analyses = (
            db.query(FashionAnalysis)
            .filter(FashionAnalysis.user_id == current_user.id)
            .order_by(FashionAnalysis.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        # Transform to match the TypeScript interface
        history = []
        for analysis in analyses:
            try:
                # Parse analysis result JSON robustly (handle strings, double-encoded JSON, and dicts)
                analysis_data = {}
                if analysis.analysis_result:
                    if isinstance(analysis.analysis_result, str):
                        try:
                            analysis_data = json.loads(analysis.analysis_result)
                        except Exception:
                            # Try double-encoded JSON
                            try:
                                analysis_data = json.loads(
                                    json.loads(analysis.analysis_result)
                                )
                            except Exception:
                                # Fallback to raw string container
                                analysis_data = {"raw": analysis.analysis_result}
                    else:
                        analysis_data = analysis.analysis_result

                # Helper to coerce numeric-like values to float safely
                def to_float(val):
                    try:
                        if val is None:
                            return 0.0
                        return float(val)
                    except Exception:
                        try:
                            return float(str(val))
                        except Exception:
                            return 0.0

                # Locate the core analysis object in multiple possible shapes
                core = None
                recommendations_blob = None

                if isinstance(analysis_data, dict):
                    # Preferred nested path: { data: { analysis: {...}, recommendations: ... } }
                    data_section = (
                        analysis_data.get("data")
                        if isinstance(analysis_data.get("data"), dict)
                        else None
                    )
                    if data_section and isinstance(data_section.get("analysis"), dict):
                        core = data_section.get("analysis")
                        recommendations_blob = data_section.get("recommendations")
                    else:
                        # Try top-level keys commonly used
                        core = (
                            analysis_data.get("analysis")
                            or analysis_data.get("result")
                            or analysis_data.get("results")
                            or analysis_data
                        )
                        recommendations_blob = (
                            analysis_data.get("recommendations")
                            or analysis_data.get("suggestions")
                            or None
                        )
                else:
                    core = {"description": str(analysis_data)}

                # Ensure core is a dict
                if not isinstance(core, dict):
                    core = {"description": str(core)}

                # Extract scores and text using several possible key names
                # Handle alternate keys produced by different analyzers (e.g. overall_rating, color_analysis)
                overall_score = (
                    to_float(
                        core.get("overall_score")
                        or core.get("style_match_score")
                        or core.get("overall")
                        or core.get("score")
                        or core.get("overall_rating")
                    )
                    * 10
                )

                color_harmony = to_float(
                    core.get("color_harmony")
                    or core.get("color_score")
                    or core.get("color_harmony_score")
                )

                style_coherence = to_float(
                    core.get("style_coherence")
                    or core.get("style_score")
                    or core.get("coherence")
                )

                # If analyzer provided textual fields like color_analysis / fit_analysis / texture_analysis,
                # include them in the human-readable analysis_text and try to infer scores from overall_rating.
                color_analysis_text = core.get("color_analysis") or core.get(
                    "color_comment"
                )
                fit_analysis_text = core.get("fit_analysis") or core.get("fit_comment")
                texture_analysis_text = core.get("texture_analysis") or core.get(
                    "texture_comment"
                )

                # If numeric color/style scores are missing, we leave them as 0.0; frontend can present textual details.
                analysis_text_candidates = [
                    core.get("description"),
                    core.get("text"),
                    core.get("summary"),
                    core.get("analysis_text"),
                ]
                # Append analyzer-specific textual parts
                if color_analysis_text:
                    analysis_text_candidates.append(color_analysis_text)
                if fit_analysis_text:
                    analysis_text_candidates.append(fit_analysis_text)
                if texture_analysis_text:
                    analysis_text_candidates.append(texture_analysis_text)

                # Also include any narrative 'improvements' string in the main text
                core_improvements_text = core.get("improvements")
                if isinstance(core_improvements_text, str) and core_improvements_text:
                    analysis_text_candidates.append(core_improvements_text)

                analysis_text = next((c for c in analysis_text_candidates if c), "")

                # Normalize recommendations into suggestions/improvements lists
                suggestions = []
                improvements = []

                def ensure_list(v):
                    if v is None:
                        return []
                    if isinstance(v, list):
                        return v
                    return [v]

                # Primary: recommendations_blob
                if recommendations_blob is not None:
                    if isinstance(recommendations_blob, dict):
                        suggestions = ensure_list(
                            recommendations_blob.get("suggestions")
                            or recommendations_blob.get("alternatives")
                            or recommendations_blob.get("items")
                        )
                        improvements = ensure_list(
                            recommendations_blob.get("improvements")
                            or recommendations_blob.get("tips")
                            or recommendations_blob.get("changes")
                        )
                        # Map common analyzer recommendation keys into our lists
                        suggestions += ensure_list(
                            recommendations_blob.get("immediate_improvements")
                        )
                        suggestions += ensure_list(
                            recommendations_blob.get("styling_alternatives")
                        )
                        suggestions += ensure_list(
                            recommendations_blob.get("styling_alternatives")
                        )
                        suggestions += ensure_list(
                            recommendations_blob.get("accessories")
                        )
                        # shopping_list is actionable items; include as suggestions too
                        suggestions += ensure_list(
                            recommendations_blob.get("shopping_list")
                        )
                    elif isinstance(recommendations_blob, list):
                        suggestions = recommendations_blob
                    else:
                        suggestions = ensure_list(recommendations_blob)

                # Secondary: check core fields for suggestions/improvements
                if not suggestions:
                    suggestions = ensure_list(
                        core.get("suggestions") or core.get("recommendations")
                    )
                if not improvements:
                    improvements = ensure_list(
                        core.get("improvements") or core.get("tips")
                    )

                # Final normalization to strings
                suggestions = [str(s) for s in suggestions if s is not None]
                improvements = [str(i) for i in improvements if i is not None]

                # Create fashion analysis object matching TypeScript interface
                created_at_iso = (
                    analysis.created_at.isoformat()
                    if getattr(analysis, "created_at", None)
                    else datetime.now(timezone.utc).isoformat()
                )

                fashion_analysis = {
                    "id": str(analysis.id),
                    "overall_score": float(overall_score) if overall_score else 0.0,
                    "color_harmony": float(color_harmony) if color_harmony else 0.0,
                    "style_coherence": float(style_coherence)
                    if style_coherence
                    else 0.0,
                    "suggestions": suggestions,
                    "improvements": improvements,
                    "analysis_text": analysis_text
                    or f"Fashion analysis performed on {datetime.fromisoformat(created_at_iso).strftime('%B %d, %Y')}",
                    "created_at": created_at_iso,
                    "user_id": str(current_user.id),
                    "image_url": None,  # Could be added later if storing image URLs
                }

                history.append(fashion_analysis)

            except Exception as e:
                # Skip invalid entries but log the error for debugging
                print(
                    f"Error processing analysis {getattr(analysis, 'id', 'unknown')}: {e}"
                )
                continue

        # Get total count for pagination
        total_count = (
            db.query(FashionAnalysis)
            .filter(FashionAnalysis.user_id == current_user.id)
            .count()
        )

        return {
            "success": True,
            "data": {
                "history": history,
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + len(history)) < total_count,
            },
            "message": f"Retrieved {len(history)} fashion analyses",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching fashion history: {str(e)}"
        )


@router.get("/wardrobe-builder/{username}")
@limit_ai_usage(
    reset_period="monthly",
    free_limit=1,
    spotlight_limit=5,
    elite_limit=20,
    icon_limit=-1,
)
async def wardrobe_builder(
    username: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    client=Depends(get_openai_client),
):
    """Generate a complete wardrobe based on user preferences"""

    # Check if user is requesting their own wardrobe or is admin
    if str(current_user.username) != username:
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        # Get user preferences from database
        from ..auth import get_user_preferences

        user_preferences = get_user_preferences(current_user)

        # Convert to UserPreferences model for compatibility
        preferences_obj = UserPreferences(
            style_preference=user_preferences.get("style_preference", "casual"),
            color_preferences=user_preferences.get("color_preferences", []),
            body_type=user_preferences.get("body_type"),
            occasion_types=user_preferences.get("occasion_types", []),
            budget_range=user_preferences.get("budget_range"),
        )

        wardrobe_plan = await generate_wardrobe_plan(client, preferences_obj)

        # Log activity
        log_user_activity(
            db=db,
            user=current_user,
            activity_type="wardrobe_builder",
            activity_data={
                "plan_generated": True,
                "style_preference": user_preferences.get("style_preference"),
            },
        )

        return {"wardrobe_plan": wardrobe_plan}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def generate_personal_style_guide(client, preferences: UserPreferences) -> dict:
    """Generate a personalized style guide using OpenAI with structured output"""

    prompt = f"""
    Create a personalized style guide for someone with these preferences:
    - Style: {", ".join(preferences.style_preference)}
    - Preferred colors: {", ".join(preferences.color_preferences)}
    - Body type: {preferences.body_type or "Not specified"}
    - Occasions: {", ".join(preferences.occasion_types)}
    - Budget: {preferences.budget_range or "Not specified"}
    - Gender: {preferences.gender or "Not specified"}
    - Country/Region: {preferences.country or "Not specified"}
    
    Consider the user's gender and cultural context when providing style recommendations.
    For country-specific advice, consider local fashion trends, climate, cultural norms, 
    and available brands/retailers in that region.
    
    Provide comprehensive style guidance including key principles, color recommendations, 
    essential wardrobe pieces, shopping priorities, and styling tips.
    """

    response = client.responses.parse(
        model="gpt-4o-2024-08-06",
        input=[
            {
                "role": "system",
                "content": "You are a fashion expert providing personalized style guidance.",
            },
            {"role": "user", "content": prompt},
        ],
        text_format=PersonalStyleGuideResponse,
    )

    content = response.output_parsed
    try:
        import json

        result = (
            content.model_dump()
            if isinstance(content, PersonalStyleGuideResponse)
            else json.loads(content)
        )
        return result
    except Exception:
        # If JSON parsing fails, return structured text
        return str(content)


async def generate_personalized_analysis(
    client, image_analysis: dict, preferences: UserPreferences
) -> dict:
    """Generate personalized analysis based on user preferences with structured output"""

    prompt = f"""
    Given this fashion analysis: {json.dumps(image_analysis)}
    
    And these user preferences:
    - Style: {", ".join(preferences.style_preference)}
    - Preferred colors: {", ".join(preferences.color_preferences)}
    - Occasions: {", ".join(preferences.occasion_types)}
    - Gender: {preferences.gender or "Not specified"}
    - Country/Region: {preferences.country or "Not specified"}
    
    Consider the user's gender and cultural context when providing recommendations.
    For country-specific advice, consider local fashion trends, climate, cultural norms.
    
    Provide personalized recommendations that align with their preferences:
    1. How well does this outfit match their style? (score 0-10)
    2. Specific adjustments for their preferences
    3. Alternative suggestions within their style
    4. How to adapt for their preferred occasions
    """

    try:
        response = client.responses.parse(
            model="gpt-4o-2024-08-06",
            input=[
                {
                    "role": "system",
                    "content": "You are a fashion expert providing personalized analysis based on user preferences and outfit analysis.",
                },
                {"role": "user", "content": prompt},
            ],
            text_format=PersonalizedAnalysis,
        )

        return response.choices[0].message.parsed.model_dump()
    except Exception:
        # Fallback to regular completion
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                    + "\n\nFormat as JSON with keys: style_match_score, personalized_adjustments, style_alternatives, occasion_adaptations.",
                }
            ],
            max_tokens=600,
        )

        try:
            return json.loads(response.choices[0].message.content)
        except Exception:
            return {"raw_analysis": response.choices[0].message.content}


async def check_style_compatibility(
    client, image_analysis: dict, preferences: UserPreferences
) -> dict:
    """Check how compatible the outfit is with user's style preferences using structured output"""

    prompt = f"""
    Rate the compatibility of this outfit analysis with user preferences:
    
    Outfit: {json.dumps(image_analysis)}
    User style: {preferences.style_preference}
    User colors: {", ".join(preferences.color_preferences)}
    
    Provide compatibility scores (1-10) for:
    1. Overall style alignment
    2. Color preference match
    3. Occasion appropriateness
    
    Also provide specific improvement suggestions.
    """

    try:
        response = client.responses.parse(
            model="gpt-4o-2024-08-06",
            input=[
                {
                    "role": "system",
                    "content": "You are a fashion expert rating outfit compatibility with user style preferences.",
                },
                {"role": "user", "content": prompt},
            ],
            text_format=StyleCompatibility,
        )

        return response.choices[0].message.parsed.model_dump()
    except Exception:
        # Fallback to regular completion
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                    + "\n\nFormat as JSON with keys: style_score, color_score, occasion_score, overall_compatibility, improvement_suggestions.",
                }
            ],
            max_tokens=400,
        )

        try:
            return json.loads(response.choices[0].message.content)
        except Exception:
            return {"raw_compatibility": response.choices[0].message.content}


async def generate_wardrobe_plan(client, preferences: UserPreferences) -> dict:
    """Generate a complete wardrobe plan using structured output"""

    prompt = f"""
    Create a comprehensive wardrobe plan for:
    - Style: {preferences.style_preference}
    - Colors: {", ".join(preferences.color_preferences)}
    - Occasions: {", ".join(preferences.occasion_types)}
    - Budget: {preferences.budget_range or "Flexible"}
    
    Include:
    1. Essential basics (tops, bottoms, outerwear)
    2. Statement pieces
    3. Accessories
    4. Seasonal additions
    5. Mix-and-match combinations
    
    Provide detailed recommendations for each category.
    """

    try:
        response = client.responses.parse(
            model="gpt-4o-2024-08-06",
            input=[
                {
                    "role": "system",
                    "content": "You are a fashion expert creating comprehensive wardrobe plans based on user preferences.",
                },
                {"role": "user", "content": prompt},
            ],
            text_format=WardrobePlan,
        )

        return response.choices[0].message.parsed.model_dump()
    except Exception:
        # Fallback to regular completion
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                    + "\n\nFormat as JSON with keys: essentials, statement_pieces, accessories, seasonal_items, outfit_combinations.",
                }
            ],
            max_tokens=1000,
        )

        try:
            return json.loads(response.choices[0].message.content)
        except Exception:
            return {"raw_plan": response.choices[0].message.content}
