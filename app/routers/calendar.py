from fastapi import APIRouter, HTTPException, Depends
import logging
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_
from datetime import datetime, timedelta

from app.decorators import limit_ai_usage
from ..dependencies import get_openai_client
from ..models import get_db, GoogleCalendarToken, WardrobeItem, OutfitPlan
from ..auth import get_current_active_user
from ..activity_tracker import log_user_activity
import json
import base64
import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

router = APIRouter(
    prefix="/calendar",
    tags=["calendar"],
    responses={404: {"description": "Not found"}},
)

templates = Jinja2Templates(directory="templates")


# Pydantic models for request/response
class GoogleTokenRequest(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    expires_in: int
    token_type: str = "Bearer"
    scope: str


class CalendarEvent(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    start_time: datetime
    end_time: datetime
    location: Optional[str] = None
    attendees: Optional[List[str]] = []


class WardrobeAddRequest(BaseModel):
    description: str  # Full description that LLM will process


class WardrobeDeleteBatchRequest(BaseModel):
    item_ids: List[int]  # List of wardrobe item IDs to delete


class WardrobeItemResponse(BaseModel):
    id: int
    category: str
    subcategory: Optional[str]
    description: Optional[str]
    color_primary: Optional[str]
    color_secondary: Optional[str]
    brand: Optional[str]
    size: Optional[str]
    season: Optional[str]
    occasion: List[str]
    image_url: Optional[str]
    tags: List[str]
    is_favorite: bool
    created_at: datetime


class MonthlyOutfitPlan(BaseModel):
    month: int
    year: int
    outfit_plans: List[Dict[str, Any]]


def encrypt_token(token: str) -> str:
    """Simple encryption for tokens (in production, use proper encryption)"""
    return base64.b64encode(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    """Simple decryption for tokens"""
    try:
        return base64.b64decode(encrypted_token.encode()).decode()
    except Exception:
        return encrypted_token


@router.get("/google-auth-url")
async def get_google_auth_url():
    """Get Google OAuth authorization URL"""
    try:
        from google_auth_oauthlib.flow import Flow

        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

        if not client_id or not client_secret:
            raise HTTPException(
                status_code=500,
                detail="Google OAuth credentials not configured. Please set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables.",
            )

        # Create OAuth flow
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        )

        # Set the redirect URI (you'll need to configure this in Google Cloud Console)
        flow.redirect_uri = "http://localhost:8000/calendar/oauth-callback"

        # Generate authorization URL
        auth_url, state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true"
        )

        return {
            "success": True,
            "data": {"auth_url": auth_url, "state": state},
            "message": "Google OAuth authorization URL generated",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error generating Google OAuth URL: {str(e)}"
        )


@router.post("/google-token")
async def save_google_token(
    token_data: GoogleTokenRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Save Google Calendar token to database"""
    try:
        # Calculate expiration time
        expires_at = datetime.now() + timedelta(seconds=token_data.expires_in)

        # Check if user already has a token
        existing_token = (
            db.query(GoogleCalendarToken)
            .filter(
                GoogleCalendarToken.user_id == current_user.id,
                GoogleCalendarToken.is_active,
            )
            .first()
        )

        if existing_token:
            # Update existing token
            existing_token.access_token = encrypt_token(token_data.access_token)
            existing_token.refresh_token = (
                encrypt_token(token_data.refresh_token)
                if token_data.refresh_token
                else None
            )
            existing_token.expires_at = expires_at
            existing_token.scope = token_data.scope
            existing_token.token_type = token_data.token_type
            existing_token.updated_at = datetime.now()

            db.commit()

            # Log activity
            log_user_activity(
                db=db,
                user=current_user,
                activity_type="google_token_updated",
                activity_data={
                    "scope": token_data.scope,
                    "expires_at": expires_at.isoformat(),
                },
            )

            return {
                "success": True,
                "message": "Google Calendar token updated successfully",
                "data": {
                    "token_id": existing_token.id,
                    "expires_at": expires_at.isoformat(),
                    "scope": token_data.scope,
                },
            }
        else:
            # Create new token
            new_token = GoogleCalendarToken(
                user_id=current_user.id,
                access_token=encrypt_token(token_data.access_token),
                refresh_token=encrypt_token(token_data.refresh_token)
                if token_data.refresh_token
                else None,
                token_type=token_data.token_type,
                expires_at=expires_at,
                scope=token_data.scope,
                is_active=True,
            )

            db.add(new_token)
            db.commit()

            # Log activity
            log_user_activity(
                db=db,
                user=current_user,
                activity_type="google_token_saved",
                activity_data={
                    "scope": token_data.scope,
                    "expires_at": expires_at.isoformat(),
                },
            )

            return {
                "success": True,
                "message": "Google Calendar token saved successfully",
                "data": {
                    "token_id": new_token.id,
                    "expires_at": expires_at.isoformat(),
                    "scope": token_data.scope,
                },
            }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error saving Google token: {str(e)}"
        )


@router.get("/oauth-callback")
async def google_oauth_callback(
    code: str,
    state: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Handle Google OAuth callback and exchange authorization code for tokens"""
    try:
        from google_auth_oauthlib.flow import Flow

        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

        if not client_id or not client_secret:
            raise HTTPException(
                status_code=500, detail="Google OAuth credentials not configured"
            )

        # Create OAuth flow
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        )

        flow.redirect_uri = "http://localhost:8000/calendar/oauth-callback"

        # Exchange authorization code for tokens
        flow.fetch_token(code=code)

        # Get credentials
        creds = flow.credentials

        # Calculate expiration time
        expires_at = datetime.now() + timedelta(seconds=3600)  # Default 1 hour
        if creds.expiry:
            expires_at = creds.expiry.replace(tzinfo=None)

        # Check if user already has a token
        existing_token = (
            db.query(GoogleCalendarToken)
            .filter(
                GoogleCalendarToken.user_id == current_user.id,
                GoogleCalendarToken.is_active,
            )
            .first()
        )

        if existing_token:
            # Update existing token
            existing_token.access_token = encrypt_token(creds.token)
            existing_token.refresh_token = (
                encrypt_token(creds.refresh_token) if creds.refresh_token else None
            )
            existing_token.expires_at = expires_at
            existing_token.scope = " ".join(creds.scopes)
            existing_token.token_type = "Bearer"
            existing_token.updated_at = datetime.now()

            db.commit()

            return {
                "success": True,
                "message": "Google Calendar connected successfully (updated existing token)",
                "data": {
                    "token_id": existing_token.id,
                    "expires_at": expires_at.isoformat(),
                    "scope": existing_token.scope,
                },
            }
        else:
            # Create new token
            new_token = GoogleCalendarToken(
                user_id=current_user.id,
                access_token=encrypt_token(creds.token),
                refresh_token=encrypt_token(creds.refresh_token)
                if creds.refresh_token
                else None,
                expires_at=expires_at,
                scope=" ".join(creds.scopes),
                token_type="Bearer",
                is_active=True,
            )

            db.add(new_token)
            db.commit()
            db.refresh(new_token)

            # Log activity
            log_user_activity(
                db=db,
                user=current_user,
                activity_type="google_calendar_connected",
                activity_data={
                    "token_id": new_token.id,
                    "scope": new_token.scope,
                    "expires_at": expires_at.isoformat(),
                },
            )

            return {
                "success": True,
                "message": "Google Calendar connected successfully",
                "data": {
                    "token_id": new_token.id,
                    "expires_at": expires_at.isoformat(),
                    "scope": new_token.scope,
                },
            }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error processing Google OAuth callback: {str(e)}"
        )


@router.get("/events")
async def get_user_events(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Fetch user's calendar events from Google Calendar API"""
    try:
        # Check if user has valid token
        token = (
            db.query(GoogleCalendarToken)
            .filter(
                GoogleCalendarToken.user_id == current_user.id,
                GoogleCalendarToken.is_active,
                GoogleCalendarToken.expires_at > datetime.now(),
            )
            .first()
        )

        if not token:
            raise HTTPException(
                status_code=403,
                detail="No valid Google Calendar token found. Please connect your calendar first.",
            )

        # Create credentials object from stored token
        try:
            access_token = decrypt_token(token.access_token)
            refresh_token = (
                decrypt_token(token.refresh_token) if token.refresh_token else None
            )
        except Exception:
            # If decryption fails, assume tokens are not encrypted (backward compatibility)
            access_token = token.access_token
            refresh_token = token.refresh_token

        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            scopes=token.scope.split(" ")
            if token.scope
            else ["https://www.googleapis.com/auth/calendar.readonly"],
        )

        # Refresh token if needed
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Update stored token in database
            token.access_token = encrypt_token(creds.token)
            if creds.expiry:
                token.expires_at = creds.expiry.replace(tzinfo=None)
            else:
                token.expires_at = datetime.now() + timedelta(seconds=3600)
            db.commit()

        # Build the Calendar API service
        service = build("calendar", "v3", credentials=creds)

        # Set default date range if not provided
        if not start_date:
            start_date = datetime.now().isoformat() + "Z"
        if not end_date:
            end_date = (datetime.now() + timedelta(days=30)).isoformat() + "Z"

        # Convert date strings to proper format if needed
        try:
            if not start_date.endswith("Z"):
                start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                start_date = start_dt.isoformat() + "Z"
            if not end_date.endswith("Z"):
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                end_date = end_dt.isoformat() + "Z"
        except ValueError:
            # If date parsing fails, use defaults
            start_date = datetime.now().isoformat() + "Z"
            end_date = (datetime.now() + timedelta(days=30)).isoformat() + "Z"

        # Call the Calendar API
        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=start_date,
                timeMax=end_date,
                maxResults=50,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events = events_result.get("items", [])

        # Transform events to our format
        formatted_events = []
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            end = event["end"].get("dateTime", event["end"].get("date"))

            # Handle all-day events (date only)
            if "T" not in start:
                start += "T00:00:00Z"
            if "T" not in end:
                end += "T23:59:59Z"

            formatted_event = {
                "id": event.get("id", ""),
                "title": event.get("summary", "Untitled Event"),
                "description": event.get("description", ""),
                "start_time": start,
                "end_time": end,
                "location": event.get("location", ""),
                "attendees": [
                    attendee.get("email", "") for attendee in event.get("attendees", [])
                ],
            }
            formatted_events.append(formatted_event)

        # Log activity
        log_user_activity(
            db=db,
            user=current_user,
            activity_type="calendar_events_fetched",
            activity_data={
                "event_count": len(formatted_events),
                "date_range": f"{start_date} to {end_date}",
            },
        )

        return {
            "events": formatted_events,
            "total_count": len(formatted_events),
            "date_range": {"start": start_date, "end": end_date},
            "message": f"Retrieved {len(formatted_events)} calendar events from Google Calendar",
        }

    except HttpError as error:
        if error.resp.status == 401:
            # Token is invalid, mark as inactive
            if token:
                token.is_active = False
                db.commit()
            raise HTTPException(
                status_code=401,
                detail="Google Calendar authentication failed. Please reconnect your calendar.",
            )
        elif error.resp.status == 403:
            raise HTTPException(
                status_code=403,
                detail="Access to Google Calendar denied. Please check your permissions.",
            )
        else:
            raise HTTPException(
                status_code=500, detail=f"Google Calendar API error: {str(error)}"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching calendar events: {str(e)}"
        )


@router.get("/wardrobe")
async def get_user_wardrobe(
    category: Optional[str] = None,
    season: Optional[str] = None,
    occasion: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Get user's wardrobe items"""
    try:
        # Build query
        query = db.query(WardrobeItem).filter(WardrobeItem.user_id == current_user.id)

        if category:
            query = query.filter(WardrobeItem.category == category)
        if season:
            query = query.filter(WardrobeItem.season.in_([season, "all"]))
        if occasion:
            query = query.filter(WardrobeItem.occasion.contains(occasion))

        # Get items with pagination
        items = query.offset(offset).limit(limit).all()
        total_count = query.count()

        # Format response
        wardrobe_items = []
        for item in items:
            try:
                occasion_list = json.loads(item.occasion) if item.occasion else []
                tags_list = json.loads(item.tags) if item.tags else []
            except (json.JSONDecodeError, TypeError):
                occasion_list = []
                tags_list = []

            wardrobe_items.append(
                {
                    "id": item.id,
                    "category": item.category,
                    "subcategory": item.subcategory,
                    "description": item.description,
                    "color_primary": item.color_primary,
                    "color_secondary": item.color_secondary,
                    "brand": item.brand,
                    "size": item.size,
                    "season": item.season,
                    "occasion": occasion_list,
                    "image_url": item.image_url,
                    "tags": tags_list,
                    "is_favorite": item.is_favorite,
                    "created_at": item.created_at.isoformat(),
                }
            )

        return {
            "wardrobe": wardrobe_items,
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
            "has_more": (offset + len(wardrobe_items)) < total_count,
            "filters": {
                "category": category,
                "season": season,
                "occasion": occasion,
            },
            "message": f"Retrieved {len(wardrobe_items)} wardrobe items",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching wardrobe: {str(e)}"
        )


@limit_ai_usage(
    reset_period="monthly",
    free_limit=1,
    spotlight_limit=5,
    elite_limit=20,
    icon_limit=-1,
)
@router.post("/wardrobe")
async def add_wardrobe_items(
    wardrobe_data: WardrobeAddRequest,
    client=Depends(get_openai_client),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Add items to wardrobe using AI processing of description"""
    # check user max wardrobe items
    from fastapi import status

    if current_user.wardrobe_items_count >= current_user.max_wardrobe_items:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Max wardrobe items limit reached",
        )

    try:
        # Use AI to process the description into structured wardrobe items
        prompt = f"""
        Process this wardrobe description into a structured list of individual clothing items:
        
        "{wardrobe_data.description}"
        
        For each item, extract:
        - category (shirt, pants, dress, shoes, jacket, etc.)
        - subcategory (t-shirt, jeans, sneakers, blazer, etc.)
        - description (detailed description)
        - color_primary (main color)
        - color_secondary (secondary color if any)
        - brand (if mentioned)
        - size (if mentioned)
        - season (spring, summer, fall, winter, or "all")
        - occasion (array of: casual, formal, business, party, sport, etc.)
        - tags (array of descriptive tags)
        
        Return as JSON array with these fields. If a field is not available, use null.
        Example:
        [
            {{
                "category": "shirt",
                "subcategory": "dress shirt",
                "description": "White cotton dress shirt with french cuffs",
                "color_primary": "white",
                "color_secondary": null,
                "brand": "Hugo Boss",
                "size": "M",
                "season": "all", 
                "occasion": ["business", "formal"],
                "tags": ["cotton", "dress shirt", "french cuffs", "professional"]
            }}
        ]
        """

        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.3,
        )

        try:
            # Parse AI response
            items_data = json.loads(response.choices[0].message.content)
        except json.JSONDecodeError:
            # Fallback: create a single item from the description
            items_data = [
                {
                    "category": "item",
                    "subcategory": None,
                    "description": wardrobe_data.description,
                    "color_primary": None,
                    "color_secondary": None,
                    "brand": None,
                    "size": None,
                    "season": "all",
                    "occasion": ["casual"],
                    "tags": ["wardrobe"],
                }
            ]

        # Save items to database
        saved_items = []
        for item_data in items_data:
            new_item = WardrobeItem(
                user_id=current_user.id,
                category=item_data.get("category", "item"),
                subcategory=item_data.get("subcategory"),
                description=item_data.get("description"),
                color_primary=item_data.get("color_primary"),
                color_secondary=item_data.get("color_secondary"),
                brand=item_data.get("brand"),
                size=item_data.get("size"),
                season=item_data.get("season", "all"),
                occasion=json.dumps(item_data.get("occasion", ["casual"])),
                tags=json.dumps(item_data.get("tags", [])),
                is_favorite=False,
            )

            db.add(new_item)
            db.flush()  # Get the ID

            saved_items.append(
                {
                    "id": new_item.id,
                    "category": new_item.category,
                    "subcategory": new_item.subcategory,
                    "description": new_item.description,
                    "color_primary": new_item.color_primary,
                    "color_secondary": new_item.color_secondary,
                    "season": new_item.season,
                    "occasion": json.loads(new_item.occasion),
                    "tags": json.loads(new_item.tags),
                }
            )

        db.commit()

        # Log activity
        log_user_activity(
            db=db,
            user=current_user,
            activity_type="wardrobe_items_added",
            activity_data={
                "items_count": len(saved_items),
                "original_description": wardrobe_data.description[:200],
            },
        )

        return {
            "success": True,
            "data": {
                "items_added": saved_items,
                "count": len(saved_items),
                "original_description": wardrobe_data.description,
            },
            "message": f"Successfully added {len(saved_items)} items to your wardrobe",
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Error adding wardrobe items: {str(e)}"
        )


@router.delete("/wardrobe/{item_id}")
async def delete_wardrobe_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Delete a wardrobe item by ID"""
    try:
        # Find the wardrobe item
        wardrobe_item = (
            db.query(WardrobeItem)
            .filter(WardrobeItem.id == item_id, WardrobeItem.user_id == current_user.id)
            .first()
        )

        if not wardrobe_item:
            raise HTTPException(
                status_code=404,
                detail="Wardrobe item not found or you don't have permission to delete it",
            )

        # Store item details for response
        deleted_item = {
            "id": wardrobe_item.id,
            "category": wardrobe_item.category,
            "subcategory": wardrobe_item.subcategory,
            "description": wardrobe_item.description,
            "color_primary": wardrobe_item.color_primary,
            "color_secondary": wardrobe_item.color_secondary,
            "brand": wardrobe_item.brand,
            "size": wardrobe_item.size,
            "season": wardrobe_item.season,
            "occasion": json.loads(wardrobe_item.occasion)
            if wardrobe_item.occasion
            else [],
            "tags": json.loads(wardrobe_item.tags) if wardrobe_item.tags else [],
            "is_favorite": wardrobe_item.is_favorite,
        }

        # Delete the item
        db.delete(wardrobe_item)
        db.commit()

        # Log activity
        log_user_activity(
            db=db,
            user=current_user,
            activity_type="wardrobe_item_deleted",
            activity_data={
                "item_id": item_id,
                "category": deleted_item["category"],
                "description": deleted_item["description"][:100]
                if deleted_item["description"]
                else "",
            },
        )

        return {
            "success": True,
            "data": {"deleted_item": deleted_item, "item_id": item_id},
            "message": f"Successfully deleted wardrobe item: {deleted_item['description'][:50]}...",
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Error deleting wardrobe item: {str(e)}"
        )


@router.get("/outfit-plans")
async def get_monthly_outfit_plans(
    month: Optional[int] = None,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """
    Get existing outfit plans for a month from database.
    If month/year are not provided, return plans for the next 30 days from today.
    """
    try:
        if month is not None and year is not None:
            if month < 1 or month > 12:
                raise HTTPException(
                    status_code=400, detail="Month must be between 1 and 12"
                )
            # Get outfit plans for the specified month and year
            outfit_plans = (
                db.query(OutfitPlan)
                .filter(
                    OutfitPlan.user_id == current_user.id,
                    db.func.extract("month", OutfitPlan.date) == month,
                    db.func.extract("year", OutfitPlan.date) == year,
                )
                .order_by(OutfitPlan.date)
                .all()
            )
        else:
            # Get outfit plans for the next 30 days from today
            today = datetime.now().date()
            end_date = today + timedelta(days=30)
            outfit_plans = (
                db.query(OutfitPlan)
                .filter(
                    OutfitPlan.user_id == current_user.id,
                    and_(
                        OutfitPlan.date >= today,
                        OutfitPlan.date <= end_date,
                    ),
                )
                .order_by(OutfitPlan.date)
                .all()
            )

        # Format response
        formatted_plans = []
        for plan in outfit_plans:
            try:
                wardrobe_item_ids = (
                    json.loads(plan.wardrobe_items) if plan.wardrobe_items else []
                )
                alternatives = (
                    json.loads(plan.alternative_suggestions)
                    if plan.alternative_suggestions
                    else []
                )
            except (json.JSONDecodeError, TypeError):
                wardrobe_item_ids = []
                alternatives = []

            formatted_plans.append(
                {
                    "id": plan.id,
                    "date": plan.date.strftime("%Y-%m-%d"),
                    "event_title": plan.event_title,
                    "event_description": plan.event_description,
                    "event_location": plan.event_location,
                    "outfit_description": plan.outfit_description,
                    "wardrobe_item_ids": wardrobe_item_ids,
                    "alternatives": alternatives,
                    "weather_considerations": plan.weather_considerations,
                    "confidence_score": plan.confidence_score,
                    "created_at": plan.created_at.isoformat()
                    if plan.created_at
                    else None,
                    "updated_at": plan.updated_at.isoformat()
                    if plan.updated_at
                    else None,
                }
            )

        msg = (
            f"Retrieved {len(formatted_plans)} outfit plans for {month}/{year}"
            if month and year
            else f"Retrieved {len(formatted_plans)} outfit plans for the next 30 days"
        )

        return {
            "success": True,
            "data": {
                "month": month,
                "year": year,
                "outfit_plans": formatted_plans,
                "total_count": len(formatted_plans),
            },
            "message": msg,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching outfit plans: {str(e)}"
        )


@limit_ai_usage(
    reset_period="monthly",
    free_limit=1,
    spotlight_limit=5,
    elite_limit=20,
    icon_limit=-1,
)
@router.post("/outfit-plans")
async def generate_monthly_outfit_plans(
    client=Depends(get_openai_client),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Generate new outfit plans for a month based on calendar events and save to database"""
    try:
        date = datetime.now()
        month = date.month
        year = date.year
        if month < 1 or month > 12:
            raise HTTPException(
                status_code=400, detail="Month must be between 1 and 12"
            )

        # Check if user has valid token for calendar access
        token = (
            db.query(GoogleCalendarToken)
            .filter(
                GoogleCalendarToken.user_id == current_user.id,
                GoogleCalendarToken.is_active,
                GoogleCalendarToken.expires_at > datetime.now(),
            )
            .first()
        )

        if not token:
            raise HTTPException(
                status_code=401,
                detail="No valid Google Calendar token found. Please connect your calendar first.",
            )

        # Get user's wardrobe items
        wardrobe_items = (
            db.query(WardrobeItem).filter(WardrobeItem.user_id == current_user.id).all()
        )

        if not wardrobe_items:
            raise HTTPException(
                status_code=400,
                detail="No wardrobe items found. Please add items to your wardrobe first.",
            )

        # Fetch real calendar events for the month from Google Calendar API
        try:
            # Create credentials object from stored token
            try:
                access_token = decrypt_token(token.access_token)
                refresh_token = (
                    decrypt_token(token.refresh_token) if token.refresh_token else None
                )
            except Exception:
                # If decryption fails, assume tokens are not encrypted (backward compatibility)
                access_token = token.access_token
                refresh_token = token.refresh_token

            creds = Credentials(
                token=access_token,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=os.getenv("GOOGLE_CLIENT_ID"),
                client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
                scopes=token.scope.split(" ")
                if token.scope
                else ["https://www.googleapis.com/auth/calendar.readonly"],
            )

            # Refresh token if needed
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                # Update stored token in database
                token.access_token = encrypt_token(creds.token)
                if creds.expiry:
                    token.expires_at = creds.expiry.replace(tzinfo=None)
                else:
                    token.expires_at = datetime.now() + timedelta(seconds=3600)
                db.commit()

            # Build the Calendar API service
            service = build("calendar", "v3", credentials=creds)

            # Set date range for the month
            start_date = f"{year}-{month:02d}-01T00:00:00Z"
            # Calculate last day of month
            if month == 12:
                end_date = f"{year + 1}-01-01T00:00:00Z"
            else:
                end_date = f"{year}-{month + 1:02d}-01T00:00:00Z"

            # Call the Calendar API
            events_result = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=start_date,
                    timeMax=end_date,
                    maxResults=100,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )

            google_events = events_result.get("items", [])

            # Transform Google Calendar events to our format
            calendar_events = []
            for event in google_events:
                start = event["start"].get("dateTime", event["start"].get("date"))

                # Extract date only for event planning
                if "T" in start:
                    event_date = start.split("T")[0]
                else:
                    event_date = start

                calendar_events.append(
                    {
                        "date": event_date,
                        "title": event.get("summary", "Untitled Event"),
                        "description": event.get("description", ""),
                        "location": event.get("location", ""),
                    }
                )

        except HttpError as error:
            # If Google Calendar API fails, use fallback events
            print(f"Google Calendar API error: {error}")
            calendar_events = [
                {
                    "date": f"{year}-{month:02d}-15",
                    "title": "Important Event",
                    "description": "Calendar sync unavailable - using fallback",
                    "location": "TBD",
                }
            ]
        except Exception as e:
            # If any other error occurs, use fallback events
            print(f"Error fetching calendar events: {e}")
            calendar_events = [
                {
                    "date": f"{year}-{month:02d}-15",
                    "title": "Important Event",
                    "description": "Calendar sync unavailable - using fallback",
                    "location": "TBD",
                }
            ]

        # Create wardrobe summary for AI
        wardrobe_summary = []
        for item in wardrobe_items:
            try:
                occasions = json.loads(item.occasion) if item.occasion else []
            except (json.JSONDecodeError, TypeError):
                occasions = []

            wardrobe_summary.append(
                {
                    "id": item.id,
                    "category": item.category,
                    "subcategory": item.subcategory,
                    "description": item.description,
                    "color_primary": item.color_primary,
                    "season": item.season,
                    "occasion": occasions,
                }
            )

        # Generate outfit plans for each event
        outfit_plans = []
        for event in calendar_events:
            # Create AI prompt for outfit planning
            prompt = f"""
            Create an outfit plan for this event:
            Event: {event["title"]}
            Description: {event["description"]}
            Location: {event["location"]}
            Date: {event["date"]}
            Season: {get_season_from_date(event["date"])}
            
            Available wardrobe items:
            {json.dumps(wardrobe_summary, indent=2)}
            
            Provide:
            1. Main outfit description
            2. Specific wardrobe item IDs to use
            3. Alternative suggestions if items aren't suitable
            4. Weather considerations
            5. Confidence score (0-100)
            
            Format as JSON:
            {{
                "outfit_description": "detailed outfit description",
                "wardrobe_item_ids": [list of item IDs],
                "alternatives": ["alternative outfit ideas"],
                "weather_considerations": "weather-based advice",
                "confidence_score": 85
            }}
            """

            try:
                response = client.chat.completions.create(
                    model="gpt-4",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=800,
                    temperature=0.3,
                )

                outfit_data = json.loads(response.choices[0].message.content)

                # Save outfit plan to database
                new_plan = OutfitPlan(
                    user_id=current_user.id,
                    date=datetime.strptime(event["date"], "%Y-%m-%d"),
                    event_title=event["title"],
                    event_description=event["description"],
                    event_location=event["location"],
                    outfit_description=outfit_data.get("outfit_description"),
                    wardrobe_items=json.dumps(outfit_data.get("wardrobe_item_ids", [])),
                    alternative_suggestions=json.dumps(
                        outfit_data.get("alternatives", [])
                    ),
                    weather_considerations=outfit_data.get("weather_considerations"),
                    confidence_score=outfit_data.get("confidence_score", 0),
                )

                db.add(new_plan)
                db.flush()

                outfit_plans.append(
                    {
                        "id": new_plan.id,
                        "date": event["date"],
                        "event_title": event["title"],
                        "event_description": event["description"],
                        "outfit_description": outfit_data.get("outfit_description"),
                        "wardrobe_item_ids": outfit_data.get("wardrobe_item_ids", []),
                        "alternatives": outfit_data.get("alternatives", []),
                        "weather_considerations": outfit_data.get(
                            "weather_considerations"
                        ),
                        "confidence_score": outfit_data.get("confidence_score", 0),
                    }
                )

            except Exception as e:
                print(f"Error generating outfit for event {event['title']}: {e}")
                continue

        db.commit()

        # Log activity
        log_user_activity(
            db=db,
            user=current_user,
            activity_type="monthly_outfit_plans_generated",
            activity_data={
                "month": month,
                "year": year,
                "plans_count": len(outfit_plans),
            },
        )

        return {
            "month": month,
            "year": year,
            "outfit_plans": outfit_plans,
            "events_count": len(calendar_events),
            "wardrobe_items_count": len(wardrobe_items),
            "message": f"Generated {len(outfit_plans)} outfit plans for {month}/{year}",
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Error generating outfit plans: {str(e)}"
        )


@router.get("/outfit-plans/single/{plan_id}")
async def get_outfit_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Get a specific outfit plan by ID"""
    try:
        # Get the outfit plan
        plan = (
            db.query(OutfitPlan)
            .filter(OutfitPlan.id == plan_id, OutfitPlan.user_id == current_user.id)
            .first()
        )

        if not plan:
            raise HTTPException(status_code=404, detail="Outfit plan not found")

        # Format response
        try:
            wardrobe_item_ids = (
                json.loads(plan.wardrobe_items) if plan.wardrobe_items else []
            )
            alternatives = (
                json.loads(plan.alternative_suggestions)
                if plan.alternative_suggestions
                else []
            )
        except (json.JSONDecodeError, TypeError):
            wardrobe_item_ids = []
            alternatives = []

        return {
            "success": True,
            "data": {
                "id": plan.id,
                "date": plan.date.strftime("%Y-%m-%d"),
                "event_title": plan.event_title,
                "event_description": plan.event_description,
                "event_location": plan.event_location,
                "outfit_description": plan.outfit_description,
                "wardrobe_item_ids": wardrobe_item_ids,
                "alternatives": alternatives,
                "weather_considerations": plan.weather_considerations,
                "confidence_score": plan.confidence_score,
                "created_at": plan.created_at.isoformat() if plan.created_at else None,
                "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
            },
            "message": "Outfit plan retrieved successfully",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching outfit plan: {str(e)}"
        )


@router.delete("/outfit-plans")
async def delete_monthly_outfit_plans(
    month: int,
    year: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Delete all outfit plans for a specific month and year"""
    try:
        if month < 1 or month > 12:
            raise HTTPException(
                status_code=400, detail="Month must be between 1 and 12"
            )

        # Delete outfit plans for the specified month and year
        deleted_count = (
            db.query(OutfitPlan)
            .filter(
                OutfitPlan.user_id == current_user.id,
                db.func.extract("month", OutfitPlan.date) == month,
                db.func.extract("year", OutfitPlan.date) == year,
            )
            .delete()
        )

        db.commit()

        # Log activity
        log_user_activity(
            db=db,
            user=current_user,
            activity_type="monthly_outfit_plans_deleted",
            activity_data={
                "month": month,
                "year": year,
                "deleted_count": deleted_count,
            },
        )

        return {
            "success": True,
            "data": {
                "month": month,
                "year": year,
                "deleted_count": deleted_count,
            },
            "message": f"Deleted {deleted_count} outfit plans for {month}/{year}",
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Error deleting outfit plans: {str(e)}"
        )


@router.delete("/outfit-plans/single/{plan_id}")
async def delete_outfit_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Delete a specific outfit plan by ID"""
    try:
        # Find and delete the outfit plan
        plan = (
            db.query(OutfitPlan)
            .filter(OutfitPlan.id == plan_id, OutfitPlan.user_id == current_user.id)
            .first()
        )

        if not plan:
            raise HTTPException(status_code=404, detail="Outfit plan not found")

        db.delete(plan)
        db.commit()

        # Log activity
        log_user_activity(
            db=db,
            user=current_user,
            activity_type="outfit_plan_deleted",
            activity_data={
                "plan_id": plan_id,
                "event_title": plan.event_title,
                "date": plan.date.strftime("%Y-%m-%d"),
            },
        )

        return {
            "success": True,
            "data": {
                "deleted_plan_id": plan_id,
            },
            "message": "Outfit plan deleted successfully",
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Error deleting outfit plan: {str(e)}"
        )


@router.get("/google-calendar/status")
async def get_connection_status(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Check Google Calendar connection status"""
    try:
        # Check if user has valid token
        token = (
            db.query(GoogleCalendarToken)
            .filter(
                GoogleCalendarToken.user_id == current_user.id,
                GoogleCalendarToken.is_active,
            )
            .first()
        )

        if not token:
            return {
                "success": True,
                "data": {
                    "connected": False,
                    "message": "No Google Calendar token found",
                },
            }

        # Check if token is expired, handle None expires_at
        if not token.expires_at:
            is_expired = True
            expires_at_str = None
        else:
            is_expired = token.expires_at < datetime.now()
            expires_at_str = token.expires_at.isoformat()

        return {
            "success": True,
            "data": {
                "connected": not is_expired,
                "token_id": token.id,
                "expires_at": expires_at_str,
                "scope": token.scope,
                "is_expired": is_expired,
                "message": "Google Calendar connected"
                if not is_expired
                else "Token expired or missing expiration, please reconnect",
            },
        }

    except Exception as e:
        logging.error(f"Error in /calendar/google-calendar/status: {e}", exc_info=True)
        print(f"[ERROR] /calendar/google-calendar/status: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error checking connection status: {str(e)}"
        )


@router.delete("/google-token")
async def disconnect_google_calendar(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Disconnect Google Calendar by deleting stored tokens"""
    try:
        # Find and delete all active tokens for the user
        deleted_count = (
            db.query(GoogleCalendarToken)
            .filter(
                GoogleCalendarToken.user_id == current_user.id,
                GoogleCalendarToken.is_active,
            )
            .delete()
        )

        db.commit()

        # Log activity
        log_user_activity(
            db=db,
            user=current_user,
            activity_type="google_calendar_disconnected",
            activity_data={
                "tokens_deleted": deleted_count,
            },
        )

        return {
            "success": True,
            "data": {"tokens_deleted": deleted_count, "disconnected": True},
            "message": f"Google Calendar disconnected successfully. Deleted {deleted_count} token(s).",
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Error disconnecting Google Calendar: {str(e)}"
        )


def get_season_from_date(date_str: str) -> str:
    """Determine season from date string"""
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        month = date_obj.month

        if month in [12, 1, 2]:
            return "winter"
        elif month in [3, 4, 5]:
            return "spring"
        elif month in [6, 7, 8]:
            return "summer"
        else:
            return "fall"
    except Exception:
        return "all"
