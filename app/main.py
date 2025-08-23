from fastapi import FastAPI, Request, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from .routers import items, users, auth, calendar
from .internal import admin
from .models import create_tables
import os
from dotenv import load_dotenv


# Load environment variables from .env file
load_dotenv()

# Create FastAPI app instance
app = FastAPI(
    title="Fashion Check",
    description="AI-powered fashion analysis and recommendation system",
    version="1.0.0",
)
print(os.getenv("CORS_ORIGINS", "No CORS origins set").split(","))

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "").split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "User-Agent",
        "DNT",
        "Cache-Control",
        "X-Mx-ReqToken",
        "Keep-Alive",
        "X-Requested-With",
        "If-Modified-Since",
        "X-CSRF-Token",
    ],
    expose_headers=["*"],
)

# Initialize database tables
create_tables()

# Create necessary directories
os.makedirs("static/uploads", exist_ok=True)
os.makedirs("templates", exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Set up templates
templates = Jinja2Templates(directory="templates")


# Custom exception handler for 401 Unauthorized
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    """Custom handler for HTTP exceptions"""

    # Check if this is a 403 error and if it should redirect
    if exc.status_code == status.HTTP_403_FORBIDDEN:
        # Check if this is a web page request (not API)
        if (
            request.url.path.startswith("/fashion/")
            or request.url.path.startswith("/users/")
            or request.url.path == "/"
        ):
            # Check if request accepts HTML (web browser)
            accept_header = request.headers.get("accept", "")
            if "text/html" in accept_header:
                # Redirect to login with return URL
                login_url = f"/auth/login?redirect={request.url.path}"
                return RedirectResponse(url=login_url, status_code=302)

    # For API requests or other errors, return default JSON response
    return await http_exception_handler(request, exc)


# Include routers
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(items.router)
app.include_router(calendar.router)
app.include_router(admin.router)


@app.get("/")
async def read_root(request: Request):
    """Root endpoint that serves the main page"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "message": "Fashion Check API is running"}


if __name__ == "__main__":
    # run in debug mode
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="debug")
