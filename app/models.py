import os
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    Text,
    ForeignKey,
    Boolean,
    Float,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.sql import func

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL", default="sqlite:///./fashcheck.db")

# Create engine
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)

# Create session
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(100), nullable=False)
    full_name = Column(String(100))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Pricing tier system
    pricing_tier = Column(String(20), default="free")  # free, spotlight, elite, icon
    subscription_start_date = Column(DateTime(timezone=True))
    subscription_end_date = Column(DateTime(timezone=True))
    subscription_status = Column(
        String(20), default="active"
    )  # active, expired, cancelled

    # User preferences as JSON
    style_preference = Column(Text)  # casual, formal, trendy, classic
    color_preferences = Column(Text)  # JSON array of colors
    body_type = Column(String(50))
    occasion_types = Column(Text)  # JSON array of occasions
    budget_range = Column(String(50))
    gender = Column(String(20))  # male, female, non-binary, prefer-not-to-say
    country = Column(String(100))  # User's country for cultural fashion context

    # Fashion analysis scores
    average_fashion_score = Column(
        Float, default=0.0
    )  # Running average of overall scores
    total_scored_analyses = Column(
        Integer, default=0
    )  # Count of analyses with valid scores

    # Relationships
    activities = relationship("UserActivity", back_populates="user")
    fashion_analyses = relationship("FashionAnalysis", back_populates="user")
    google_tokens = relationship("GoogleCalendarToken", back_populates="user")
    wardrobe_items = relationship("WardrobeItem", back_populates="user")
    outfit_plans = relationship("OutfitPlan", back_populates="user")
    style_guides = relationship("PersonalStyleGuide", back_populates="user")


class UserActivity(Base):
    __tablename__ = "user_activities"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    activity_type = Column(
        String(50), nullable=False
    )  # upload_analyze, camera_analyze, style_suggestions, etc.
    activity_data = Column(Text)  # JSON data of the activity
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    ip_address = Column(String(45))  # For tracking
    user_agent = Column(String(500))

    # Relationships
    user = relationship("User", back_populates="activities")


class FashionAnalysis(Base):
    __tablename__ = "fashion_analyses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    analysis_type = Column(String(50), nullable=False)
    image_data = Column(Text)  # Base64 image data or file path
    analysis_result = Column(Text)  # JSON analysis result
    recommendations = Column(Text)  # JSON recommendations
    rating = Column(Integer)  # User rating of the analysis
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="fashion_analyses")


class GoogleCalendarToken(Base):
    __tablename__ = "google_calendar_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    access_token = Column(Text, nullable=False)  # Encrypted access token
    refresh_token = Column(Text)  # Encrypted refresh token
    token_type = Column(String(50), default="Bearer")
    expires_at = Column(DateTime(timezone=True))
    scope = Column(Text)  # Comma-separated scopes
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    is_active = Column(Boolean, default=True)

    # Relationships
    user = relationship("User", back_populates="google_tokens")


class WardrobeItem(Base):
    __tablename__ = "wardrobe_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    category = Column(String(50), nullable=False)  # shirt, pants, dress, shoes, etc.
    subcategory = Column(String(50))  # t-shirt, jeans, sneakers, etc.
    description = Column(Text)  # Full description of the item
    color_primary = Column(String(50))
    color_secondary = Column(String(50))
    brand = Column(String(100))
    size = Column(String(20))
    season = Column(String(20))  # spring, summer, fall, winter, all
    occasion = Column(Text)  # JSON array of occasions: casual, formal, business, etc.
    image_url = Column(Text)  # Optional image URL or base64
    tags = Column(Text)  # JSON array of tags
    purchase_date = Column(DateTime(timezone=True))
    price = Column(Float)
    is_favorite = Column(Boolean, default=False)
    last_worn_date = Column(DateTime(timezone=True))
    is_available = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="wardrobe_items")


class OutfitPlan(Base):
    __tablename__ = "outfit_plans"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)
    event_title = Column(String(200))
    event_description = Column(Text)
    event_location = Column(String(200))
    outfit_description = Column(Text, nullable=False)  # AI-generated outfit description
    wardrobe_items = Column(Text)  # JSON array of wardrobe item IDs
    alternative_suggestions = Column(Text)  # JSON array of alternative outfit ideas
    weather_considerations = Column(Text)  # Weather-based adjustments
    confidence_score = Column(Float)  # AI confidence in outfit suggestion
    user_rating = Column(Integer)  # User rating of the outfit (1-5)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="outfit_plans")


""" class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text)
    location = Column(String(200))
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    provider = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="calendar_events")
    outfit_plans = relationship("OutfitPlan", back_populates="calendar_event")
 """


class PersonalStyleGuide(Base):
    __tablename__ = "personal_style_guides"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    style_principles = Column(Text)  # JSON array of style principles
    color_palette = Column(Text)  # JSON array of recommended colors
    essential_pieces = Column(Text)  # JSON array of essential wardrobe pieces
    shopping_priorities = Column(Text)  # JSON array of shopping priorities
    styling_tips = Column(Text)  # JSON array of styling tips
    preferences_snapshot = Column(
        Text
    )  # JSON snapshot of user preferences when guide was created
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    is_active = Column(Boolean, default=True)  # Only one active guide per user

    # Relationships
    user = relationship("User", back_populates="style_guides")


class SessionToken(Base):
    __tablename__ = "session_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token_hash = Column(String(255), unique=True, index=True)
    expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_revoked = Column(Boolean, default=False)


# Dependency to get database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Create tables
def create_tables():
    Base.metadata.create_all(bind=engine)
