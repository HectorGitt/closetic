from fastapi import APIRouter, HTTPException, Depends
import logging
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_
from datetime import datetime, timedelta, timezone

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


class WardrobeItemCreate(BaseModel):
    category: str
    subcategory: Optional[str] = None
    description: Optional[str] = None
    color_primary: Optional[str] = None
    color_secondary: Optional[str] = None
    brand: Optional[str] = None
    size: Optional[str] = None
    season: Optional[str] = None
    occasion: List[str] = []
    tags: List[str] = []
    favorite: bool = False


class WardrobeDeleteBatchRequest(BaseModel):
    item_ids: List[int]  # List of wardrobe item IDs to delete


class WardrobeWornRequest(BaseModel):
    date: str  # Date in YYYY-MM-DD format


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


def refresh_google_token_if_needed(token, db, force_refresh=False):
    """
    Given a GoogleCalendarToken SQLAlchemy object, refresh the token if expired or force_refresh is True.
    Returns (creds, refreshed: bool, error: Optional[str])
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    import os
    from datetime import datetime, timezone, timedelta

    try:
        access_token = decrypt_token(token.access_token)
        refresh_token = (
            decrypt_token(token.refresh_token) if token.refresh_token else None
        )
    except Exception:
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

    # Determine if refresh is needed
    expired = getattr(creds, "expired", False)
    needs_refresh = (expired or force_refresh) and creds.refresh_token

    if needs_refresh:
        try:
            creds.refresh(Request())
            # Update stored token
            token.access_token = encrypt_token(creds.token)
            if creds.expiry:
                if (
                    getattr(creds.expiry, "tzinfo", None) is None
                    or creds.expiry.tzinfo.utcoffset(creds.expiry) is None
                ):
                    token.expires_at = creds.expiry.replace(tzinfo=timezone.utc)
                else:
                    token.expires_at = creds.expiry.astimezone(timezone.utc)
            else:
                token.expires_at = datetime.now(timezone.utc) + timedelta(seconds=3600)
            token.updated_at = datetime.now(timezone.utc)
            db.commit()
            return creds, True, None
        except Exception as e:
            return creds, False, f"Error refreshing token: {str(e)}"
    return creds, False, None


def _to_rfc3339_z(dt: datetime) -> str:
    if getattr(dt, "tzinfo", None) is None or dt.tzinfo.utcoffset(dt) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    # Remove +00:00 and use trailing Z which Google expects
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
        # Calculate expiration time (store as UTC-aware datetime)
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=token_data.expires_in
        )

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
            # store UTC-aware expires_at and updated_at
            existing_token.expires_at = expires_at
            existing_token.scope = token_data.scope
            existing_token.token_type = token_data.token_type
            existing_token.updated_at = datetime.now(timezone.utc)

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

        # Calculate expiration time (store as UTC-aware)
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=3600
        )  # Default 1 hour
        if creds.expiry:
            # Normalize creds.expiry to UTC-aware
            if (
                getattr(creds.expiry, "tzinfo", None) is None
                or creds.expiry.tzinfo.utcoffset(creds.expiry) is None
            ):
                expires_at = creds.expiry.replace(tzinfo=timezone.utc)
            else:
                expires_at = creds.expiry.astimezone(timezone.utc)

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
            existing_token.updated_at = datetime.now(timezone.utc)

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
            creds, refreshed, error = refresh_google_token_if_needed(token, db)
            if error:
                raise HTTPException(status_code=403, detail=error)
            if refreshed:
                print("Token was refreshed.")

        # Build the Calendar API service
        service = build("calendar", "v3", credentials=creds)

        # Set default date range if not provided. Caller-supplied dates are assumed
        # to be in a valid format; we only ensure our defaults are RFC3339 Z.
        if not start_date:
            start_date = _to_rfc3339_z(datetime.now(timezone.utc))
        if not end_date:
            end_date = _to_rfc3339_z(datetime.now(timezone.utc) + timedelta(days=30))

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
                status_code=403,
                detail="Google Calendar authentication failed. Please reconnect your calendar.",
            )
        elif error.resp.status == 403:
            raise HTTPException(
                status_code=403,
                detail="Access to Google Calendar denied. Please check your permissions.",
            )
        else:
            logging.error(f"Google Calendar API error: {error}", exc_info=True)
            raise HTTPException(
                status_code=500, detail=f"Google Calendar API error: {str(error)}"
            )
    except HTTPException as e:
        logging.error(f"HTTP error in /calendar/events: {e}", exc_info=True)
        raise
    except Exception as e:
        logging.error(f"Error in /calendar/events: {e}", exc_info=True)
        print(f"[ERROR] /calendar/events: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error fetching calendar events: {str(e)}"
        )


@router.put("/wardrobe/{item_id}/worn")
async def mark_wardrobe_item_worn(
    item_id: int,
    worn_data: WardrobeWornRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Toggle wardrobe item as worn on a specific date"""
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
                detail="Wardrobe item not found or you don't have permission to access it",
            )

        # Parse the date (supports both date-only and full datetime formats)
        try:
            if wardrobe_item.is_available:
                # Try parsing as full datetime first, then fall back to date-only
                try:
                    # Simple parsing - just handle the date as-is
                    worn_date = datetime.fromisoformat(
                        worn_data.date.replace("Z", "+00:00")
                    )
                    print("Parsed as full datetime:", worn_date)
                    if worn_date.tzinfo is None:
                        worn_date = worn_date.replace(tzinfo=timezone.utc)
                    print("Worn date with timezone:", worn_date)
                except ValueError:
                    # Fall back to date-only parsing
                    worn_date = datetime.strptime(worn_data.date, "%Y-%m-%d")
                    worn_date = worn_date.replace(tzinfo=timezone.utc)

                wardrobe_item.last_worn_date = worn_date
                wardrobe_item.is_available = False
            else:
                wardrobe_item.is_available = True

        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid date format. Please use YYYY-MM-DD format",
            )

        # Update the item
        wardrobe_item.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(wardrobe_item)

        print("Wardrobe item updated:", wardrobe_item.last_worn_date)

        # Log activity
        log_user_activity(
            db=db,
            user=current_user,
            activity_type="wardrobe_item_worn",
            activity_data={
                "item_id": item_id,
                "worn_date": worn_data.date,
                "category": wardrobe_item.category,
                "description": wardrobe_item.description[:100]
                if wardrobe_item.description
                else "",
            },
        )

        return {
            "success": True,
            "data": {
                "id": wardrobe_item.id,
                "last_worn_date": _to_rfc3339_z(wardrobe_item.last_worn_date)
                if wardrobe_item.last_worn_date
                else None,
                "is_available": wardrobe_item.is_available,
                "updated_at": wardrobe_item.updated_at.isoformat(),
            },
            "message": f"Successfully marked {wardrobe_item.description[:50]}... as worn on {worn_data.date}",
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Error marking wardrobe item as worn: {str(e)}"
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
                    "last_worn_date": _to_rfc3339_z(item.last_worn_date)
                    if item.last_worn_date
                    else None,
                    "is_available": item.is_available,
                    "created_at": _to_rfc3339_z(item.created_at)
                    if item.created_at
                    else None,
                }
            )

        return {
            "success": True,
            "data": {
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
            },
            "message": f"Retrieved {len(wardrobe_items)} wardrobe items",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching wardrobe: {str(e)}"
        )


@router.post("/wardrobe")
@limit_ai_usage(
    reset_period="monthly",
    free_limit=2,
    spotlight_limit=10,
    elite_limit=30,
    icon_limit=-1,
)
async def add_wardrobe_items(
    wardrobe_data: WardrobeAddRequest,
    client=Depends(get_openai_client),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Add items to wardrobe using AI processing of description"""
    # check user max wardrobe items
    from fastapi import status

    # Determine max allowed wardrobe items:
    # prefer explicit per-user override (current_user.max_wardrobe_items),
    # otherwise ask the users router for tier limits via get_tier_features.
    user_override = getattr(current_user, "max_wardrobe_items", None)
    if user_override is not None:
        try:
            max_allowed = int(user_override)
        except Exception:
            max_allowed = None
    else:
        # Import locally to avoid potential circular imports at module import time
        try:
            from ..routers.users import get_tier_features

            tier = getattr(current_user, "pricing_tier", None) or "free"
            tier_features = get_tier_features(tier)
            # The users helper uses 'max_wardrobe_items' as the key
            max_allowed = tier_features.get("max_wardrobe_items", None)
        except Exception:
            # Fallback conservative defaults if users helper not available
            tier_fallback = {
                "free": 50,
                "spotlight": 200,
                "elite": 500,
                "icon": -1,
            }
            tier = getattr(current_user, "pricing_tier", None) or "free"
            max_allowed = tier_fallback.get(str(tier).lower(), tier_fallback["free"])

    # Treat None as unlimited as a safe default
    if max_allowed is None:
        max_allowed = -1

    # Ensure we have a numeric count to compare
    current_count = (
        db.query(WardrobeItem).filter(WardrobeItem.user_id == current_user.id).count()
        or 0
    )

    # Enforce limit (skip if unlimited)
    if max_allowed != -1 and current_count >= max_allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Max wardrobe items limit reached ({current_count}/{max_allowed})",
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

        If a line specifies a number of items (e.g., “2 White Shirts”), you must create that many separate JSON objects, each representing one clothing item.
        
        Return as JSON array with these fields. If a field is not available, use null.
        Example do not enclose in ```json``` blocks:
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
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )

        try:
            # Parse AI response
            items_data = json.loads(response.choices[0].message.content)
        except json.JSONDecodeError:
            # Fallback: create a single item from the description
            """ items_data = [
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
            ] """
            raise HTTPException(
                status_code=500,
                detail="AI response parsing error: Invalid JSON format",
            )

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
                    "last_worn_date": _to_rfc3339_z(new_item.last_worn_date)
                    if new_item.last_worn_date
                    else None,
                    "is_available": new_item.is_available,
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


@router.post("/wardrobe/item_add")
async def add_wardrobe_item(
    item: WardrobeItemCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Add a new item to the wardrobe"""
    try:
        new_item = WardrobeItem(
            user_id=current_user.id,
            category=item.category,
            subcategory=item.subcategory,
            description=item.description,
            color_primary=item.color_primary,
            color_secondary=item.color_secondary,
            brand=item.brand,
            size=item.size,
            season=item.season,
            occasion=json.dumps(item.occasion),
            tags=json.dumps(item.tags),
            is_favorite=item.favorite,
        )

        db.add(new_item)
        db.commit()
        db.refresh(new_item)

        return {
            "success": True,
            "data": {
                "id": new_item.id,
                "category": new_item.category,
                "subcategory": new_item.subcategory,
                "description": new_item.description,
                "color_primary": new_item.color_primary,
                "color_secondary": new_item.color_secondary,
                "brand": new_item.brand,
                "size": new_item.size,
                "season": new_item.season,
                "occasion": json.loads(new_item.occasion),
                "tags": json.loads(new_item.tags),
                "is_favorite": new_item.is_favorite,
                "last_worn_date": _to_rfc3339_z(new_item.last_worn_date)
                if new_item.last_worn_date
                else None,
                "is_available": new_item.is_available,
            },
            "message": "Successfully added new wardrobe item",
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Error adding wardrobe item: {str(e)}"
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
            today = datetime.now(timezone.utc).date()
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
                    "created_at": _to_rfc3339_z(plan.created_at)
                    if plan.created_at
                    else None,
                    "updated_at": _to_rfc3339_z(plan.updated_at)
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


@router.post("/outfit-plans")
@limit_ai_usage(
    reset_period="monthly",
    free_limit=2,
    spotlight_limit=8,
    elite_limit=25,
    icon_limit=-1,
)
async def generate_monthly_outfit_plans(
    client=Depends(get_openai_client),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Generate new outfit plans for a month based on calendar events and save to database"""
    try:
        date = datetime.now(timezone.utc)
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
            )
            .first()
        )

        if not token:
            raise HTTPException(
                status_code=403,
                detail="No valid Google Calendar token found. Please connect your calendar first.",
            )
        creds, refreshed, error = refresh_google_token_if_needed(token, db)
        token = creds if refreshed else token

        current_outfit_plans = (
            db.query(OutfitPlan).filter(OutfitPlan.user_id == current_user.id).all()
        )

        if len(current_outfit_plans) > 0 and current_user.pricing_tier == "free":
            return {
                "success": False,
                "status": 403,
                "data": {
                    "message": "Free tier users can only have one outfit plan generation."
                },
            }

        # Get user's wardrobe items
        wardrobe_items = (
            db.query(WardrobeItem)
            .filter(WardrobeItem.user_id == current_user.id, WardrobeItem.is_available)
            .all()
        )

        if not wardrobe_items:
            if (
                db.query(WardrobeItem)
                .filter(WardrobeItem.user_id == current_user.id)
                .all()
            ):
                detail = "No available wardrobe items found, Clean up your closet"
            else:
                detail = (
                    "No wardrobe items found. Please add items to your wardrobe first."
                )

            raise HTTPException(
                status_code=400,
                detail=detail,
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
                    # Normalize creds.expiry to UTC-aware
                    if (
                        getattr(creds.expiry, "tzinfo", None) is None
                        or creds.expiry.tzinfo.utcoffset(creds.expiry) is None
                    ):
                        token.expires_at = creds.expiry.replace(tzinfo=timezone.utc)
                    else:
                        token.expires_at = creds.expiry.astimezone(timezone.utc)
                else:
                    token.expires_at = datetime.now(timezone.utc) + timedelta(
                        seconds=3600
                    )
                db.commit()

            # Build the Calendar API service
            service = build("calendar", "v3", credentials=creds)

            # Set date range for the month
            # Set date range: today to 30 days from now (UTC, RFC3339 Z format)
            today = datetime.now(timezone.utc)
            start_date = (
                today.replace(hour=0, minute=0, second=0, microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
            end_date = (
                (today + timedelta(days=30))
                .replace(hour=23, minute=59, second=59, microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )

            # Call the Calendar API
            events_result = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=start_date,
                    timeMax=end_date,
                    maxResults=15,
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

        except HttpError as e:
            # If Google Calendar API fails, use fallback events
            print(f"Google Calendar API error: {e}")
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

        # Generate outfit plans for all events in a single OpenAI call
        outfit_plans = []
        # Prepare prompt for all events
        prompt = f"""
        For each of the following calendar events, generate an outfit plan using the available wardrobe items. 
        For each event, provide detailed information for:
        1. Main outfit description (specific combination and description of wardrobe items mentioning the items, colors, and styles)
        2. Specific wardrobe item IDs to use
        3. Alternative suggestions if items aren't suitable
        4. Weather considerations
        5. Confidence score (0-100)

        Format your response as a JSON array, where each element corresponds to an event and includes:
        {{
            "date": "YYYY-MM-DD",
            "event_title": "...",
            "event_description": "...",
            "outfit_description": "...",
            "wardrobe_item_ids": [list of item IDs],
            "alternatives": ["..."],
            "weather_considerations": "...",
            "confidence_score": 85
        }}

        Calendar Events:
        {json.dumps(calendar_events[:10], indent=2)}

        Available wardrobe items:
        {json.dumps(wardrobe_summary, indent=2)}

        Do not enclose your response in a ```json block.
        """

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            print(f"AI response for all events: {response.choices[0].message.content}")
            all_outfits = json.loads(response.choices[0].message.content)
        except Exception as e:
            print(f"Error generating outfits for events: {e}")
            raise HTTPException(
                status_code=500, detail=f"Error generating outfit plans: {str(e)}"
            )

        # Save each outfit plan to database
        for event, outfit_data in zip(calendar_events[:10], all_outfits):
            try:
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
                print(f"Error saving outfit plan for event {event['title']}: {e}")
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


@router.post("/outfit-plans/single")
@limit_ai_usage(
    reset_period="monthly",
    free_limit=3,
    spotlight_limit=12,
    elite_limit=40,
    icon_limit=-1,
)
async def generate_outfit_plan_for_event(
    event: CalendarEvent,
    db: Session = Depends(get_db),
    client=Depends(get_openai_client),
    current_user=Depends(get_current_active_user),
):
    """Generate a single outfit plan for a specific event"""
    # Get all outfit plans within 7 days (before or after) of the event date to avoid duplicate outfits
    # Ensure event.start_time is a datetime object
    event_date = event.start_time
    if isinstance(event_date, str):
        try:
            event_date = datetime.fromisoformat(event_date)
        except Exception:
            event_date = (
                datetime.strptime(event_date, "%Y-%m-%dT%H:%M:%S%z")
                if "T" in event_date
                else datetime.strptime(event_date, "%Y-%m-%d")
            )
    event_date = event_date.date() if hasattr(event_date, "date") else event_date

    week_start = event_date - timedelta(days=3)
    week_end = event_date + timedelta(days=3)

    existing_plans = (
        db.query(OutfitPlan)
        .filter(
            OutfitPlan.user_id == current_user.id,
            OutfitPlan.date >= week_start,
            OutfitPlan.date <= week_end,
        )
        .all()
    )
    plan_summary = []
    for plan in existing_plans:
        plan_summary.append(
            {
                "id": plan.id,
                "date": str(plan.date),
                "event_title": plan.event_title,
                "event_description": plan.event_description,
                "outfit_description": plan.outfit_description,
                "wardrobe_item_ids": json.loads(plan.wardrobe_items)
                if plan.wardrobe_items
                else [],
                "alternatives": json.loads(plan.alternative_suggestions)
                if plan.alternative_suggestions
                else [],
                "weather_considerations": plan.weather_considerations,
                "confidence_score": plan.confidence_score,
            }
        )

    wardrobe_items = (
        db.query(WardrobeItem)
        .filter(WardrobeItem.user_id == current_user.id, WardrobeItem.is_available)
        .all()
    )
    if not wardrobe_items:
        raise HTTPException(status_code=400, detail="No available wardrobe items found")

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

    try:
        prompt = f"""
        For the following calendar event, generate an outfit plan using the available wardrobe items. 
        For the event, provide detailed information for:
        1. Main outfit description (specific combination and description of wardrobe items mentioning the items, colors, and styles)
        2. Specific wardrobe item IDs to use
        3. Alternative suggestions if items aren't suitable
        4. Weather considerations
        5. Confidence score (0-100)

        Format your response as a JSON object, where each element corresponds to an event and includes:
        {{
            "date": "YYYY-MM-DD",
            "event_title": "...",
            "event_description": "...",
            "outfit_description": "...",
            "wardrobe_item_ids": [list of item IDs],
            "alternatives": ["..."],
            "weather_considerations": "...",
            "confidence_score": 85
        }}

        Calendar Event:
        {event.model_dump()}

        Available wardrobe items:
        {json.dumps(wardrobe_summary, indent=2)}

        Current outfit plans:
        {json.dumps(plan_summary, indent=2)}

        Do not enclose your response in a ```json block.
        """

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            print(f"AI response for event: {response.choices[0].message.content}")
            outfit_plan = json.loads(response.choices[0].message.content)

            # Check if an outfit plan already exists for this specific event
            existing_plan = (
                db.query(OutfitPlan)
                .filter(
                    OutfitPlan.user_id == current_user.id,
                    OutfitPlan.date == event_date,
                    OutfitPlan.event_title == event.title,
                )
                .first()
            )

            if existing_plan:
                # Update the existing plan with new AI recommendations
                existing_plan.outfit_description = outfit_plan.get(
                    "outfit_description", ""
                )
                existing_plan.wardrobe_items = json.dumps(
                    outfit_plan.get("wardrobe_item_ids", [])
                )
                existing_plan.alternative_suggestions = json.dumps(
                    outfit_plan.get("alternatives", [])
                )
                existing_plan.weather_considerations = outfit_plan.get(
                    "weather_considerations", ""
                )
                existing_plan.confidence_score = outfit_plan.get("confidence_score", 0)
                existing_plan.updated_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(existing_plan)

                return {
                    "success": True,
                    "data": {
                        "id": existing_plan.id,
                        "date": existing_plan.date.strftime("%Y-%m-%d"),
                        "event_title": existing_plan.event_title,
                        "event_description": existing_plan.event_description,
                        "event_location": existing_plan.event_location,
                        "outfit_description": existing_plan.outfit_description,
                        "wardrobe_item_ids": json.loads(existing_plan.wardrobe_items)
                        if existing_plan.wardrobe_items
                        else [],
                        "alternatives": json.loads(
                            existing_plan.alternative_suggestions
                        )
                        if existing_plan.alternative_suggestions
                        else [],
                        "weather_considerations": existing_plan.weather_considerations,
                        "confidence_score": existing_plan.confidence_score,
                        "created_at": existing_plan.created_at.isoformat()
                        if existing_plan.created_at
                        else None,
                        "updated_at": existing_plan.updated_at.isoformat()
                        if existing_plan.updated_at
                        else None,
                    },
                    "message": "Outfit plan updated successfully with new AI recommendations",
                }
            else:
                # Create a new plan
                new_plan = OutfitPlan(
                    user_id=current_user.id,
                    date=event_date,
                    event_title=event.title,
                    event_description=event.description,
                    outfit_description=outfit_plan.get("outfit_description", ""),
                    wardrobe_items=json.dumps(outfit_plan.get("wardrobe_item_ids", [])),
                    alternative_suggestions=json.dumps(
                        outfit_plan.get("alternatives", [])
                    ),
                    weather_considerations=outfit_plan.get(
                        "weather_considerations", ""
                    ),
                    confidence_score=outfit_plan.get("confidence_score", 0),
                )
                db.add(new_plan)
                db.commit()
                db.refresh(new_plan)

                return {
                    "success": True,
                    "data": {
                        "id": new_plan.id,
                        "date": new_plan.date.strftime("%Y-%m-%d"),
                        "event_title": new_plan.event_title,
                        "event_description": new_plan.event_description,
                        "event_location": new_plan.event_location,
                        "outfit_description": new_plan.outfit_description,
                        "wardrobe_item_ids": json.loads(new_plan.wardrobe_items)
                        if new_plan.wardrobe_items
                        else [],
                        "alternatives": json.loads(new_plan.alternative_suggestions)
                        if new_plan.alternative_suggestions
                        else [],
                        "weather_considerations": new_plan.weather_considerations,
                        "confidence_score": new_plan.confidence_score,
                        "created_at": new_plan.created_at.isoformat()
                        if new_plan.created_at
                        else None,
                        "updated_at": new_plan.updated_at.isoformat()
                        if new_plan.updated_at
                        else None,
                    },
                    "message": "Outfit plan created successfully",
                }
        except Exception as e:
            print(f"Error generating outfits for event: {e}")
            raise HTTPException(
                status_code=500, detail=f"Error generating outfit plan: {str(e)}"
            )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Error generating outfit plan: {str(e)}"
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

        # Check if token is expired, handle None expires_at and timezone-aware values
        if not token.expires_at:
            is_expired = True
            expires_at_str = None
        else:
            expires_at = token.expires_at
            # If token.expires_at is timezone-aware, compare with aware UTC now
            if (
                getattr(expires_at, "tzinfo", None) is not None
                and expires_at.tzinfo.utcoffset(expires_at) is not None
            ):
                now = datetime.now(timezone.utc)
            else:
                # Naive datetime: compare with naive UTC now (normalize to UTC)
                now = datetime.now(timezone.utc)

            try:
                is_expired = expires_at < now
            except TypeError:
                # As a fallback, normalize both to UTC-aware
                try:
                    expires_aware = (
                        expires_at.replace(tzinfo=timezone.utc)
                        if getattr(expires_at, "tzinfo", None) is None
                        else expires_at.astimezone(timezone.utc)
                    )
                    now_aware = datetime.now(timezone.utc)
                    is_expired = expires_aware < now_aware
                except Exception:
                    # If all else fails, mark expired to force reconnect
                    is_expired = True

            try:
                expires_at_str = expires_at.isoformat()
            except Exception:
                expires_at_str = None

        """ return {
            "success": True,
            "data": {
                "connected": not is_expired,
                "token_id": token.id,
                "expires_at": expires_at_str,
                "scope": token.scope,
                "is_expired": is_expired,
                "message": "Google Calendar connected"
                if not is_expired
                else "Token expired or missing expiration, attempting refresh if possible",
            },
        } """

        if is_expired:
            try:
                # Attempt to construct credentials and refresh
                try:
                    access_token = decrypt_token(token.access_token)
                    refresh_token = (
                        decrypt_token(token.refresh_token)
                        if token.refresh_token
                        else None
                    )
                except Exception:
                    access_token = token.access_token
                    refresh_token = token.refresh_token

                if refresh_token:
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

                    # Only attempt refresh if credentials report expired and have a refresh token
                    if creds.refresh_token and (
                        getattr(creds, "expired", False) or is_expired
                    ):
                        creds.refresh(Request())

                        # Update stored token
                        token.access_token = encrypt_token(creds.token)
                        if creds.expiry:
                            if (
                                getattr(creds.expiry, "tzinfo", None) is None
                                or creds.expiry.tzinfo.utcoffset(creds.expiry) is None
                            ):
                                token.expires_at = creds.expiry.replace(
                                    tzinfo=timezone.utc
                                )
                            else:
                                token.expires_at = creds.expiry.astimezone(timezone.utc)
                        else:
                            token.expires_at = datetime.now(timezone.utc) + timedelta(
                                seconds=3600
                            )

                        token.updated_at = datetime.now(timezone.utc)
                        db.commit()

                        expires_at_str = (
                            token.expires_at.isoformat() if token.expires_at else None
                        )

                        return {
                            "success": True,
                            "data": {
                                "connected": True,
                                "token_id": token.id,
                                "expires_at": expires_at_str,
                                "scope": token.scope,
                                "is_expired": False,
                                "message": "Token refreshed and calendar reconnected",
                            },
                        }
                # If we get here, refresh not possible
                print("Refresh token not available or expired")
                return {
                    "success": True,
                    "data": {
                        "connected": False,
                        "token_id": token.id,
                        "expires_at": expires_at_str,
                        "scope": token.scope,
                        "is_expired": True,
                        "message": "Token expired and refresh not available; please reconnect",
                    },
                }
            except Exception as e:
                logging.error(f"Error refreshing Google token: {e}", exc_info=True)
                return {
                    "success": False,
                    "data": {"connected": False, "token_id": token.id},
                    "message": f"Error refreshing token: {str(e)}",
                }

        else:
            return {
                "success": True,
                "data": {
                    "connected": True,
                    "token_id": token.id,
                    "expires_at": expires_at_str,
                    "scope": token.scope,
                    "is_expired": False,
                    "message": "Calendar Connected",
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
