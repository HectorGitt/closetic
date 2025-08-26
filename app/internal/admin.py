from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from ..dependencies import get_openai_client
from ..models import get_db, User
from ..auth import get_current_active_user
from ..activity_tracker import get_activity_stats
from pydantic import BaseModel
from typing import List, Dict
import json

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
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Serve the admin dashboard"""
    # For now, any authenticated user can access admin (add proper admin check in production)
    return templates.TemplateResponse(
        "admin_dashboard.html", {"request": request, "user": current_user}
    )


@router.get("/analytics")
async def get_analytics(
    current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)
):
    """Get fashion analysis analytics"""

    # Get overall statistics from database
    from sqlalchemy import func
    from ..models import UserActivity, FashionAnalysis

    # Total analyses count
    total_analyses = db.query(FashionAnalysis).count()

    # Total users count
    total_users = db.query(User).count()

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

    return AnalyticsData(
        total_analyses=total_analyses,
        popular_styles=[f"{item[0]}: {item[1]}" for item in analysis_breakdown[:5]],
        common_issues=[f"{item[0]}: {item[1]}" for item in activity_breakdown[:5]],
        user_satisfaction=8.5,  # Default satisfaction score
    )


@router.post("/feedback")
async def record_feedback(
    analysis_result: dict,
    user_rating: int,
    current_user: User = Depends(get_current_active_user),
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

    return {"message": "Feedback recorded successfully"}


@router.get("/trends")
async def get_fashion_trends(
    client=Depends(get_openai_client),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Get current fashion trends analysis"""

    try:
        prompt = """
        Provide a comprehensive analysis of current fashion trends including:
        1. Top 5 trending styles this season
        2. Popular color combinations
        3. Emerging fashion movements
        4. Sustainable fashion trends
        5. Predictions for next season
        
        Format as JSON with keys: trending_styles, popular_colors, emerging_movements, sustainability_trends, predictions.
        """

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
        )

        # Log user activity
        from ..activity_tracker import log_user_activity

        log_user_activity(db, current_user, "trends_view", {})

        try:
            return json.loads(response.choices[0].message.content)
        except Exception:
            return {"raw_trends": response.choices[0].message.content}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/style-database")
async def get_style_database(
    current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)
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

    return style_database


@router.get("/user-insights")
async def get_user_insights(
    current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)
):
    """Get insights about user behavior and preferences"""

    # Log user activity
    from ..activity_tracker import log_user_activity

    log_user_activity(db, current_user, "user_insights_view", {})

    # Get insights from database
    from sqlalchemy import func
    from ..models import UserActivity, FashionAnalysis

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

    return insights
