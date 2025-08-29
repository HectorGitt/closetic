from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime, time, timezone

from app.decorators import limit_ai_usage
from ..dependencies import (
    get_openai_client,
    process_image,
    analyze_fashion_with_openai,
    FashionAnalysisResponse,
)
from ..models import get_db
from ..auth import get_current_active_user
from ..activity_tracker import log_user_activity, save_fashion_analysis
from pydantic import BaseModel
from typing import Optional
import json

router = APIRouter(
    prefix="/fashion",
    tags=["fashion"],
    responses={404: {"description": "Not found"}},
)

templates = Jinja2Templates(directory="templates")


class CameraAnalysisRequest(BaseModel):
    image_data: str
    analysis_type: Optional[str] = "comprehensive"


class ChatbotRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    include_wardrobe: Optional[bool] = True
    include_preferences: Optional[bool] = True
    include_events: Optional[bool] = True


class ChatbotResponse(BaseModel):
    response: str
    conversation_id: str
    message_count: int
    remaining_messages: int


async def get_fashion_history(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Get user's fashion analysis history"""
    try:
        from ..models import FashionAnalysis
        import json

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
                # Parse analysis result JSON
                analysis_data = {}
                if analysis.analysis_result:
                    if isinstance(analysis.analysis_result, str):
                        analysis_data = json.loads(analysis.analysis_result)
                    else:
                        analysis_data = analysis.analysis_result

                # Extract scores and data from nested structure
                overall_score = 0
                color_harmony = 0
                style_coherence = 0
                suggestions = []
                improvements = []
                analysis_text = ""

                # Handle different JSON structures
                if isinstance(analysis_data, dict):
                    # Try nested data.analysis structure
                    if "data" in analysis_data and "analysis" in analysis_data["data"]:
                        inner_analysis = analysis_data["data"]["analysis"]
                        overall_score = inner_analysis.get("overall_score", 0)
                        color_harmony = inner_analysis.get("color_harmony", 0)
                        style_coherence = inner_analysis.get("style_coherence", 0)
                        analysis_text = inner_analysis.get("description", "")

                        # Get recommendations
                        if "recommendations" in analysis_data["data"]:
                            recommendations = analysis_data["data"]["recommendations"]
                            if isinstance(recommendations, dict):
                                suggestions = recommendations.get("suggestions", [])
                                improvements = recommendations.get("improvements", [])
                            elif isinstance(recommendations, list):
                                suggestions = recommendations

                    # Try direct structure
                    else:
                        overall_score = analysis_data.get("overall_score", 0)
                        color_harmony = analysis_data.get("color_harmony", 0)
                        style_coherence = analysis_data.get("style_coherence", 0)
                        analysis_text = analysis_data.get("description", "")
                        suggestions = analysis_data.get("suggestions", [])
                        improvements = analysis_data.get("improvements", [])

                # Ensure suggestions and improvements are lists
                if not isinstance(suggestions, list):
                    suggestions = [str(suggestions)] if suggestions else []
                if not isinstance(improvements, list):
                    improvements = [str(improvements)] if improvements else []

                # Create fashion analysis object matching TypeScript interface
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
                    or f"Fashion analysis performed on {analysis.created_at.strftime('%B %d, %Y')}",
                    "created_at": analysis.created_at.isoformat(),
                    "user_id": str(current_user.id),
                    "image_url": None,  # Could be added later if storing image URLs
                }

                history.append(fashion_analysis)

            except Exception as e:
                # Skip invalid entries but log the error
                print(f"Error processing analysis {analysis.id}: {e}")
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


router = APIRouter(
    prefix="/fashion",
    tags=["fashion"],
    responses={404: {"description": "Not found"}},
)

templates = Jinja2Templates(directory="templates")


@router.get("/analyze")
async def fashion_analyze_page(
    request: Request, current_user=Depends(get_current_active_user)
):
    """Serve the fashion analysis page"""
    return templates.TemplateResponse(
        "analyze.html", {"request": request, "user": current_user}
    )


@router.post("/upload-analyze", response_model=FashionAnalysisResponse)
@limit_ai_usage(
    reset_period="monthly",
    free_limit=1,
    spotlight_limit=5,
    elite_limit=20,
    icon_limit=-1,
)
async def upload_and_analyze(
    request: Request,
    file: UploadFile = File(...),
    analysis_type: str = "comprehensive",
    client=Depends(get_openai_client),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Upload an image and get fashion analysis"""

    # Validate file type
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    try:
        # Process the image
        image_base64 = process_image(file)

        # Analyze with OpenAI
        analysis = await analyze_fashion_with_openai(
            client, image_base64, analysis_type
        )

        # Generate additional recommendations
        # recommendations = await generate_recommendations(client, analysis)

        # Save analysis to database
        save_fashion_analysis(
            db=db,
            user=current_user,
            analysis_type=analysis_type,
            analysis_result=analysis,
            image_data=image_base64[:100] + "..."
            if len(image_base64) > 100
            else image_base64,  # Store truncated for space
        )

        # Update user's average fashion score
        update_user_average_score(db, current_user.id, analysis)

        # Log user activity
        client_ip = request.client.host if request and request.client else None
        user_agent = request.headers.get("user-agent") if request else None

        log_user_activity(
            db=db,
            user=current_user,
            activity_type="upload_analyze",
            activity_data={
                "analysis_type": analysis_type,
                "file_type": file.content_type,
                "file_size": file.size if hasattr(file, "size") else 0,
            },
            ip_address=client_ip,
            user_agent=user_agent,
        )

        return analysis

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/camera-analyze", response_model=FashionAnalysisResponse)
@limit_ai_usage(
    reset_period="monthly",
    free_limit=0,
    spotlight_limit=5,
    elite_limit=20,
    icon_limit=-1,
)
async def analyze_camera_capture(
    request: Request,
    file: UploadFile = File(...),
    client=Depends(get_openai_client),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Analyze fashion from camera capture"""

    try:
        # Process camera frame
        image_base64 = process_image(file)

        # Use default analysis type if not provided
        analysis_type = "comprehensive"

        # Analyze with OpenAI
        analysis = await analyze_fashion_with_openai(
            client, image_base64, analysis_type
        )

        # Generate additional recommendations
        # recommendations = await generate_recommendations(client, analysis)

        # Save analysis to database
        save_fashion_analysis(
            db=db,
            user=current_user,
            analysis_type=analysis_type,
            analysis_result=analysis,
            image_data=image_base64[:100] + "..."
            if len(image_base64) > 100
            else image_base64,
        )

        # Update user's average fashion score
        update_user_average_score(db, current_user.id, analysis)

        # Log user activity
        log_user_activity(
            db=db,
            user=current_user,
            activity_type="camera_analyze",
            activity_data={"analysis_type": analysis_type, "capture_method": "camera"},
        )

        return analysis

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/style-suggestions/{style_type}")
@limit_ai_usage(
    reset_period="monthly",
    free_limit=1,
    spotlight_limit=5,
    elite_limit=20,
    icon_limit=-1,
)
async def get_style_suggestions(
    style_type: str,
    client=Depends(get_openai_client),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Get general style suggestions by type"""

    style_prompts = {
        "casual": "Provide 5 casual outfit ideas with color combinations and styling tips",
        "formal": "Provide 5 formal outfit ideas suitable for business or events",
        "seasonal": "Provide seasonal fashion recommendations for current trends",
        "color-trends": "Provide current color trend analysis and recommendations",
    }

    prompt = style_prompts.get(style_type, "Provide general fashion advice")

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )

        suggestions = response.choices[0].message.content

        # Log user activity
        log_user_activity(
            db=db,
            user=current_user,
            activity_type="style_suggestions",
            activity_data={"style_type": style_type, "prompt_used": prompt},
        )

        return {"suggestions": suggestions}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analysis-status")
async def get_analysis_status(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Check if user can perform analysis today"""
    try:
        from ..models import FashionAnalysis

        # Get start and end of today
        today = datetime.now(timezone.utc).date()
        start_of_day = datetime.combine(today, time.min)
        end_of_day = datetime.combine(today, time.max)

        # Check today's analyses
        today_analyses = (
            db.query(FashionAnalysis)
            .filter(
                FashionAnalysis.user_id == current_user.id,
                FashionAnalysis.created_at >= start_of_day,
                FashionAnalysis.created_at <= end_of_day,
            )
            .all()
        )

        analyses_today = len(today_analyses)
        can_analyze = analyses_today < 1

        # Get the latest analysis info if exists
        latest_analysis = None
        if today_analyses:
            latest = max(today_analyses, key=lambda x: x.created_at)
            latest_analysis = {
                "time": latest.created_at.isoformat(),
                "type": latest.analysis_type,
            }

        # Calculate time until next analysis allowed
        next_reset = None
        if not can_analyze:
            # Use timedelta to safely get tomorrow (handles month/year rollovers)
            from datetime import time as _time, timedelta as _timedelta

            tomorrow_date = today + _timedelta(days=1)
            tomorrow = datetime.combine(tomorrow_date, _time.min)
            next_reset = tomorrow.isoformat()

        return {
            "success": True,
            "data": {
                "can_analyze": can_analyze,
                "analyses_today": analyses_today,
                "daily_limit": 1,
                "latest_analysis": latest_analysis,
                "next_reset": next_reset,
                "message": "Daily analysis available"
                if can_analyze
                else "Daily limit reached",
            },
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error checking analysis status: {str(e)}"
        )


async def generate_recommendations(client, analysis: dict) -> dict:
    """Generate additional recommendations based on analysis"""
    try:
        # Create a prompt based on the analysis
        prompt = f"""
        Based on this fashion analysis: {json.dumps(analysis)}
        
        Provide specific actionable recommendations:
        1. Immediate improvements (what to change now)
        2. Shopping suggestions (specific items to buy)
        3. Styling alternatives (different ways to wear current items)
        4. Color palette suggestions
        5. Accessory recommendations
        
        Format as JSON with keys: immediate_improvements, shopping_list, styling_alternatives, color_palette, accessories.
        """

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )

        try:
            return json.loads(response.choices[0].message.content)
        except Exception:
            return {"raw_recommendations": response.choices[0].message.content}

    except Exception as e:
        return {"error": f"Could not generate recommendations: {str(e)}"}


@router.get("/camera")
async def camera_page(request: Request, current_user=Depends(get_current_active_user)):
    """Serve the camera page for live fashion analysis"""
    return templates.TemplateResponse(
        "camera.html", {"request": request, "user": current_user}
    )


@router.get("/leaderboard")
async def get_fashion_leaderboard(
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """Get fashion analysis leaderboard (no authentication required)"""
    try:
        from sqlalchemy import desc
        from ..models import User
        from datetime import datetime

        # Query to get users with most fashion analysis activities
        leaderboard_query = (
            db.query(
                User.username,
                User.full_name,
                User.total_scored_analyses,
                User.updated_at,
            )
            .filter(
                User.total_scored_analyses.isnot(None),
                User.total_scored_analyses > 0,
            )
            .order_by(desc(User.total_scored_analyses))
            .limit(limit)
        )

        results = leaderboard_query.all()

        # Format the leaderboard data
        leaderboard = []
        for rank, (username, full_name, analysis_count, last_activity) in enumerate(
            results, 1
        ):
            leaderboard.append(
                {
                    "rank": rank,
                    "username": username,
                    "display_name": full_name or username,
                    "analysis_count": analysis_count or 0,
                    "last_activity": last_activity.isoformat()
                    if last_activity
                    else None,
                    "badge": get_user_badge(analysis_count or 0),
                }
            )

        return {
            "success": True,
            "data": {
                "leaderboard": leaderboard,
                "total_users": len(leaderboard),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "message": "Leaderboard retrieved successfully",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching leaderboard: {str(e)}"
        )


@router.get("/fashion-icon")
async def get_fashion_icon_leaderboard(
    min_analyses: int = 1,
    db: Session = Depends(get_db),
):
    """Get fashion icon leaderboard based on highest average scores (no authentication required)"""
    try:
        from sqlalchemy import desc
        from ..models import User
        from datetime import datetime

        # Query to get users with highest average scores from User table
        icon_query = (
            db.query(
                User.username,
                User.full_name,
                User.average_fashion_score,
                User.total_scored_analyses,
                User.updated_at,
            )
            .filter(
                User.average_fashion_score.isnot(None),
                User.average_fashion_score > 0,
                User.total_scored_analyses >= min_analyses,
            )
            .order_by(desc(User.average_fashion_score))
            .limit(1)  # Only get the top fashion icon
        )

        result = icon_query.first()

        if not result:
            return {
                "success": True,
                "data": {
                    "fashion_icon": None,
                    "message": f"No qualifying users found with valid scores (minimum {min_analyses} analyses required)",
                },
                "message": "No fashion icon found",
            }

        username, full_name, avg_score, total_analyses, last_updated = result

        # Format the fashion icon data
        fashion_icon = {
            "username": username,
            "display_name": full_name or username,
            "total_scored_analyses": total_analyses,
            "avg_overall_score": round(float(avg_score), 2),
            "last_updated": last_updated.isoformat() if last_updated else None,
            "icon": get_fashion_icon_badge(avg_score),
            "title": "🌟 Fashion Icon of the Month",
        }

        return {
            "success": True,
            "data": {
                "fashion_icon": fashion_icon,
                "criteria": f"Highest average overall score with minimum {min_analyses} analyses",
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "message": "Fashion icon retrieved successfully",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching fashion icon: {str(e)}"
        )


def get_fashion_icon_badge(avg_score: float) -> dict:
    """Determine fashion icon badge based on average score"""
    if avg_score >= 95:
        return {"name": "Fashion Legend", "color": "diamond", "icon": "💎"}
    elif avg_score >= 90:
        return {"name": "Style Master", "color": "platinum", "icon": "🏆"}
    elif avg_score >= 85:
        return {"name": "Fashion Icon", "color": "gold", "icon": "👑"}
    elif avg_score >= 80:
        return {"name": "Style Star", "color": "silver", "icon": "⭐"}
    else:
        return {"name": "Rising Star", "color": "bronze", "icon": "🌟"}


def get_user_badge(analysis_count: int) -> dict:
    """Determine user badge based on analysis count"""
    if analysis_count >= 100:
        return {"name": "Fashion Expert", "color": "gold", "icon": "👑"}
    elif analysis_count >= 50:
        return {"name": "Style Guru", "color": "silver", "icon": "⭐"}
    elif analysis_count >= 25:
        return {"name": "Fashion Enthusiast", "color": "bronze", "icon": "🎯"}
    elif analysis_count >= 10:
        return {"name": "Style Explorer", "color": "blue", "icon": "🔍"}
    else:
        return {"name": "Fashion Newbie", "color": "green", "icon": "🌱"}


def update_user_average_score(db: Session, user_id: int, analysis_result: dict):
    """Calculate and update user's average fashion score after each analysis"""
    try:
        from ..models import User

        # Extract overall rating from analysis result
        overall_rating = None
        print(analysis_result)

        if isinstance(analysis_result, dict):
            # Try different possible paths for overall rating
            if "data" in analysis_result and "analysis" in analysis_result["data"]:
                overall_rating = analysis_result["data"]["analysis"].get(
                    "overall_rating"
                )
            elif "overall_rating" in analysis_result:
                overall_rating = analysis_result["overall_rating"]
            elif "analysis" in analysis_result:
                overall_rating = analysis_result["analysis"].get("overall_rating")

        if overall_rating is not None:
            print(f"Extracted overall score: {overall_rating}")
            overall_rating = float(overall_rating)

            # Get user from database
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                return

            # Initialize values if None
            current_avg = user.average_fashion_score or 0.0
            current_count = user.total_scored_analyses or 0

            # Convert overall_rating from 10-scale to 100-scale
            overall_rating_100 = float(overall_rating) * 10

            # Calculate new average using incremental formula
            # new_avg = (old_avg * old_count + new_rating) / (old_count + 1)
            new_count = current_count + 1
            new_avg = (current_avg * current_count + overall_rating_100) / new_count
            print(
                f"Current avg: {current_avg}, Count: {current_count}, New rating (100-scale): {overall_rating_100}, New avg: {new_avg}"
            )

            # Update user record
            user.average_fashion_score = round(new_avg, 2)
            user.total_scored_analyses = new_count
            print(
                f"Updating user {user_id} average score: {new_avg:.2f} (from {current_avg:.2f})"
            )

            db.commit()

            print(
                f"Updated user {user_id} average score: {new_avg:.2f} (from {current_avg:.2f})"
            )

    except Exception as e:
        print(f"Error updating user average score: {e}")
        db.rollback()


@router.post("/chatbot")
@limit_ai_usage(
    reset_period="daily",
    free_limit=5,
    spotlight_limit=20,
    elite_limit=50,
    icon_limit=-1,
)
async def fashion_chatbot(
    request: ChatbotRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
    client=Depends(get_openai_client),
):
    """AI Fashion Chatbot with user preferences and wardrobe context"""

    try:
        # Get user context
        context = await get_user_fashion_context(
            db,
            current_user,
            request.include_wardrobe,
            request.include_preferences,
            request.include_events,
        )

        # Build conversation prompt with context
        system_prompt = build_chatbot_system_prompt(context)

        # Create conversation messages
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request.message},
        ]

        # Get AI response
        response = client.chat.completions.create(
            model="gpt-4o-mini", messages=messages, temperature=0.7
        )

        ai_response = response.choices[0].message.content

        # Generate conversation ID if not provided
        conversation_id = (
            request.conversation_id
            or f"chat_{current_user.id}_{int(__import__('time').time())}"
        )

        # Get usage statistics
        from ..decorators import check_ai_usage_status

        usage_stats = check_ai_usage_status(
            user=current_user,
            endpoint_name="fashion_chatbot",
            db=db,
            reset_period="daily",
            free_limit=5,
            spotlight_limit=20,
            elite_limit=50,
            icon_limit=-1,
        )

        # Log chatbot activity
        log_user_activity(
            db=db,
            user=current_user,
            activity_type="chatbot_interaction",
            activity_data={
                "message_length": len(request.message),
                "response_length": len(ai_response),
                "conversation_id": conversation_id,
                "context_included": {
                    "wardrobe": request.include_wardrobe,
                    "preferences": request.include_preferences,
                },
                "usage_count": usage_stats["current_usage"] + 1,
                "tier": current_user.pricing_tier,
            },
        )

        return {
            "success": True,
            "data": ChatbotResponse(
                response=ai_response,
                conversation_id=conversation_id,
                # context_used=context,
                message_count=usage_stats["current_usage"] + 1,
                remaining_messages=usage_stats["remaining"] - 1
                if usage_stats["remaining"] > 0
                else 0,
            ),
            "message": "Chatbot response generated successfully",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chatbot error: {str(e)}")


async def get_user_fashion_context(
    db: Session,
    user,
    include_wardrobe: bool = True,
    include_preferences: bool = True,
    include_events: bool = True,
) -> dict:
    """Get user's fashion context including preferences and wardrobe"""

    context = {
        "user_info": {"username": user.username, "pricing_tier": user.pricing_tier}
    }

    # Get user preferences
    if include_preferences:
        try:
            from ..auth import get_user_preferences

            preferences = get_user_preferences(user, db)
            context["preferences"] = {
                "style_preference": preferences.get("style_preference", []),
                "color_preferences": preferences.get("color_preferences", []),
                "body_type": preferences.get("body_type", ""),
                "occasion_types": preferences.get("occasion_types", []),
                "budget_range": preferences.get("budget_range", ""),
            }

            # Include personal style guide if available
            if preferences.get("personal_style_guide"):
                style_guide = preferences["personal_style_guide"]
                if hasattr(style_guide, "style_principles"):
                    context["style_guide"] = {
                        "style_principles": json.loads(style_guide.style_principles)
                        if style_guide.style_principles
                        else [],
                        "color_palette": json.loads(style_guide.color_palette)
                        if style_guide.color_palette
                        else [],
                        "essential_pieces": json.loads(style_guide.essential_pieces)
                        if style_guide.essential_pieces
                        else [],
                        "styling_tips": json.loads(style_guide.styling_tips)
                        if style_guide.styling_tips
                        else [],
                    }
        except Exception as e:
            print(f"Error getting preferences: {e}")
            context["preferences"] = {}

    # Get wardrobe items
    if include_wardrobe:
        try:
            from ..models import WardrobeItem

            wardrobe_items = (
                db.query(WardrobeItem)
                .filter(WardrobeItem.user_id == user.id)
                .limit(20)
                .all()
            )  # Limit to recent items to avoid context overflow

            context["wardrobe"] = []
            for item in wardrobe_items:
                context["wardrobe"].append(
                    {
                        "category": item.category,
                        "subcategory": item.subcategory,
                        "description": item.description,
                        "color_primary": item.color_primary,
                        "color_secondary": item.color_secondary,
                        "brand": item.brand,
                        "season": item.season,
                        "occasion": json.loads(item.occasion) if item.occasion else [],
                        "tags": json.loads(item.tags) if item.tags else [],
                        "is_favorite": item.is_favorite,
                    }
                )
        except Exception as e:
            print(f"Error getting wardrobe: {e}")
            context["wardrobe"] = []

    return context


def build_chatbot_system_prompt(context: dict) -> str:
    """Build system prompt with user context"""

    prompt = """You are a professional fashion stylist and personal shopping assistant. You provide personalized fashion advice, styling tips, outfit recommendations, and wardrobe guidance.

Your expertise includes:
- Personal styling and outfit coordination
- Color theory and seasonal palettes
- Body type considerations
- Occasion-appropriate dressing
- Fashion trends and timeless pieces
- Shopping recommendations within budget
- Wardrobe organization and optimization
- Cultural fashion norms and regional trends
- Gender-inclusive styling advice

Always be encouraging, specific, and actionable in your advice. Consider the user's personal style, preferences, existing wardrobe, gender identity, and cultural context when making recommendations.

"""

    # Add user context
    if "user_info" in context:
        user_info = context["user_info"]
        prompt += f"\nUser Information:\n- Username: {user_info['username']}\n- Subscription Tier: {user_info['pricing_tier']}\n"

    # Add preferences context
    if "preferences" in context and context["preferences"]:
        prefs = context["preferences"]
        prompt += "\nUser Style Preferences:\n"
        if prefs.get("style_preference"):
            prompt += f"- Style Types: {', '.join(prefs['style_preference'])}\n"
        if prefs.get("color_preferences"):
            prompt += f"- Preferred Colors: {', '.join(prefs['color_preferences'])}\n"
        if prefs.get("body_type"):
            prompt += f"- Body Type: {prefs['body_type']}\n"
        if prefs.get("occasion_types"):
            prompt += f"- Occasions: {', '.join(prefs['occasion_types'])}\n"
        if prefs.get("budget_range"):
            prompt += f"- Budget Range: {prefs['budget_range']}\n"
        if prefs.get("gender"):
            prompt += f"- Gender: {prefs['gender']}\n"
        if prefs.get("country"):
            prompt += f"- Country/Region: {prefs['country']}\n"

    # Add style guide context
    if "style_guide" in context:
        style_guide = context["style_guide"]
        prompt += "\nPersonal Style Guide:\n"
        if style_guide.get("style_principles"):
            prompt += (
                f"- Style Principles: {', '.join(style_guide['style_principles'])}\n"
            )
        if style_guide.get("color_palette"):
            prompt += (
                f"- Personal Color Palette: {', '.join(style_guide['color_palette'])}\n"
            )
        if style_guide.get("essential_pieces"):
            prompt += (
                f"- Essential Pieces: {', '.join(style_guide['essential_pieces'])}\n"
            )

    # Add wardrobe context
    if "wardrobe" in context and context["wardrobe"]:
        prompt += f"\nCurrent Wardrobe ({len(context['wardrobe'])} items):\n"

        # Group by category for better organization
        categories = {}
        for item in context["wardrobe"]:
            cat = item["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(item)

        for category, items in categories.items():
            prompt += f"\n{category.title()}:\n"
            for item in items[:5]:  # Limit items per category
                item_desc = f"  - {item['description']}"
                if item.get("color_primary"):
                    item_desc += f" ({item['color_primary']}"
                    if item.get("color_secondary"):
                        item_desc += f"/{item['color_secondary']}"
                    item_desc += ")"
                if item.get("brand"):
                    item_desc += f" by {item['brand']}"
                if item.get("is_favorite"):
                    item_desc += " ⭐"
                prompt += item_desc + "\n"

    prompt += """\n
Based on this information, provide personalized fashion advice. Reference specific items from their wardrobe when relevant, and suggest combinations that align with their style preferences and occasions they dress for.

Keep responses conversational, helpful, and specific to their needs. and no markdown like *** or # 

Quit the intro and be concise and direct in your responses."""

    return prompt


@router.get("/chatbot/usage")
async def get_chatbot_usage(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Get chatbot usage statistics for the current user"""

    try:
        from ..decorators import check_ai_usage_status

        usage_stats = check_ai_usage_status(
            user=current_user,
            endpoint_name="fashion_chatbot",
            db=db,
            reset_period="daily",
            free_limit=5,
            spotlight_limit=20,
            elite_limit=50,
            icon_limit=-1,
        )

        # Get tier information
        tier_info = {
            "free": {
                "name": "Free",
                "daily_limit": 5,
                "features": ["Basic chatbot access"],
            },
            "spotlight": {
                "name": "Spotlight",
                "daily_limit": 20,
                "features": ["Enhanced chatbot access", "Wardrobe context"],
            },
            "elite": {
                "name": "Elite",
                "daily_limit": 50,
                "features": [
                    "Premium chatbot access",
                    "Full context",
                    "Priority responses",
                ],
            },
            "icon": {
                "name": "Icon",
                "daily_limit": "Unlimited",
                "features": ["Unlimited chatbot access", "All features"],
            },
        }

        current_tier_info = tier_info.get(current_user.pricing_tier, tier_info["free"])

        return {
            "success": True,
            "data": {
                "current_usage": usage_stats["current_usage"],
                "daily_limit": usage_stats["limit"]
                if usage_stats["limit"] != -1
                else "unlimited",
                "remaining_messages": usage_stats["remaining"]
                if usage_stats["remaining"] != -1
                else "unlimited",
                "reset_time": usage_stats["reset_time"],
                "tier": current_user.pricing_tier,
                "tier_info": current_tier_info,
                "unlimited": usage_stats["unlimited"],
            },
            "message": f"Chatbot usage for {current_user.pricing_tier} tier",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error getting chatbot usage: {str(e)}"
        )


@router.get("/chatbot/features")
async def get_chatbot_features(
    current_user=Depends(get_current_active_user),
):
    """Get available chatbot features based on user's pricing tier"""

    features = {
        "free": {
            "daily_messages": 5,
            "features": [
                "Basic fashion advice",
                "General styling tips",
                "Simple outfit suggestions",
            ],
            "limitations": ["Limited context awareness", "Basic responses only"],
        },
        "spotlight": {
            "daily_messages": 20,
            "features": [
                "Enhanced fashion advice",
                "Wardrobe-aware suggestions",
                "Color coordination tips",
                "Seasonal recommendations",
            ],
            "limitations": ["Limited conversation history"],
        },
        "elite": {
            "daily_messages": 50,
            "features": [
                "Premium fashion consultation",
                "Full wardrobe integration",
                "Personal style guide integration",
                "Shopping recommendations",
                "Detailed outfit planning",
                "Trend analysis",
            ],
            "limitations": [],
        },
        "icon": {
            "daily_messages": "unlimited",
            "features": [
                "Unlimited fashion consultation",
                "Priority AI responses",
                "Advanced styling algorithms",
                "Personal shopper experience",
                "Custom fashion insights",
                "Exclusive trend previews",
            ],
            "limitations": [],
        },
    }

    user_features = features.get(current_user.pricing_tier, features["free"])

    return {
        "success": True,
        "data": {
            "tier": current_user.pricing_tier,
            "tier_name": current_user.pricing_tier.title(),
            "features": user_features,
            "upgrade_available": current_user.pricing_tier != "icon",
        },
        "message": f"Chatbot features for {current_user.pricing_tier} tier",
    }
