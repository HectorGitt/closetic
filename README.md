# Fashion Check - AI-Powered Fashion Analysis Platform

[![FastAPI](https://img.shields.io/badge/FastAPI-0.104.1-009688.svg?style=flat&logo=FastAPI&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg?style=flat&logo=python&logoColor=white)](https://python.org)
[![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4-412991.svg?style=flat&logo=openai&logoColor=white)](https://openai.com)

A comprehensive AI-powered fashion analysis and recommendation system that helps users improve their style through intelligent outfit analysis, personalized recommendations, and wardrobe building.

## 🌟 Features

### Core Features

-   **AI Fashion Analysis**: Upload photos for detailed outfit analysis using GPT-4 Vision
-   **Live Camera Analysis**: Real-time fashion analysis through webcam integration
-   **Personalized Recommendations**: Tailored advice based on user preferences and body type
-   **Wardrobe Builder**: Complete wardrobe planning with mix-and-match suggestions
-   **Style Database**: Comprehensive fashion reference with color theory and body type guides

### User Management

-   **JWT Authentication**: Secure user registration and login system
-   **User Profiles**: Comprehensive style preference management
-   **Activity Tracking**: Detailed logging of user interactions and analysis history
-   **Dashboard**: Personal analytics and style insights

### Admin Features

-   **Analytics Dashboard**: User behavior and fashion trend analytics
-   **Feedback System**: User rating collection for continuous improvement
-   **Fashion Trends**: Current trend analysis and predictions
-   **User Insights**: Comprehensive user behavior analytics

## 🚀 Quick Start

### Prerequisites

-   Python 3.8 or higher
-   OpenAI API key
-   Virtual environment (recommended)

### Installation

1. **Clone the repository**

```bash
git clone <repository-url>
cd fashcheck
```

2. **Create and activate virtual environment**

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

3. **Install dependencies**

```bash
pip install -r requirements.txt
```

4. **Set up environment variables**

Create a `.env` file in the project root:

```env
# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key_here

# Security
SECRET_KEY=your_secret_key_here
JWT_SECRET_KEY=your_jwt_secret_key_here

# Database
DATABASE_URL=sqlite:///./fashcheck.db

# Application
DEBUG=True
HOST=localhost
PORT=8000
```

5. **Initialize the database**

```bash
python -c "from app.models import create_tables; create_tables()"
```

6. **Run the application**

```bash
python run.py
# or
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

7. **Access the application**

-   Web Interface: http://localhost:8000
-   API Documentation: http://localhost:8000/docs
-   Alternative API Docs: http://localhost:8000/redoc

## 📁 Project Structure

```
fashcheck/
├── app/
│   ├── main.py              # FastAPI application entry point
│   ├── auth.py              # Authentication utilities
│   ├── models.py            # Database models and schemas
│   ├── dependencies.py      # Shared dependencies
│   ├── activity_tracker.py  # User activity logging
│   ├── routers/
│   │   ├── auth.py          # Authentication endpoints
│   │   ├── users.py         # User management endpoints
│   │   └── items.py         # Fashion analysis endpoints
│   └── internal/
│       └── admin.py         # Admin panel endpoints
├── templates/               # HTML templates
├── static/                  # Static files (CSS, JS, images)
├── requirements.txt         # Python dependencies
├── .env                     # Environment variables
├── run.py                   # Application runner
└── README.md               # This file
```

## 🛠️ Technology Stack

### Backend

-   **FastAPI**: Modern, fast web framework for building APIs
-   **SQLAlchemy**: SQL toolkit and Object-Relational Mapping
-   **SQLite**: Lightweight database for development
-   **JWT**: JSON Web Tokens for authentication
-   **bcrypt**: Password hashing

### AI & ML

-   **OpenAI GPT-4**: Advanced language model for fashion analysis
-   **GPT-4 Vision**: Image analysis capabilities
-   **Pillow**: Image processing library
-   **OpenCV**: Computer vision library

### Frontend

-   **Jinja2**: Template engine for HTML rendering
-   **Bootstrap 5**: CSS framework for responsive design
-   **JavaScript**: Client-side interactivity

## 📖 API Documentation

Comprehensive API documentation is available at:

-   **Interactive Docs**: http://localhost:8000/docs (Swagger UI)
-   **Alternative Docs**: http://localhost:8000/redoc (ReDoc)
-   **Detailed Documentation**: [API_DOCUMENTATION.md](API_DOCUMENTATION.md)

### Key Endpoints

#### Authentication

-   `POST /auth/register` - User registration
-   `POST /auth/login` - User login
-   `GET /auth/me` - Get current user info
-   `PUT /auth/preferences` - Update user preferences

#### Fashion Analysis

-   `POST /fashion/upload-analyze` - Analyze uploaded image
-   `POST /fashion/camera-analyze` - Analyze camera capture
-   `GET /fashion/style-suggestions/{type}` - Get style suggestions

#### User Management

-   `GET /users/profile` - User profile page
-   `POST /users/preferences` - Save user preferences
-   `GET /users/wardrobe-builder/{username}` - Build wardrobe

#### Admin

-   `GET /admin/analytics` - Get analytics data
-   `POST /admin/feedback` - Record user feedback
-   `GET /admin/trends` - Get fashion trends

## 🔧 Configuration

### Environment Variables

| Variable         | Description                     | Default                        |
| ---------------- | ------------------------------- | ------------------------------ |
| `OPENAI_API_KEY` | OpenAI API key for GPT-4 access | Required                       |
| `SECRET_KEY`     | Application secret key          | `your_secret_key_here`         |
| `JWT_SECRET_KEY` | JWT signing key                 | `fashion-check-jwt-secret-key` |
| `DATABASE_URL`   | Database connection string      | `sqlite:///./fashcheck.db`     |
| `DEBUG`          | Debug mode                      | `True`                         |
| `HOST`           | Server host                     | `localhost`                    |
| `PORT`           | Server port                     | `8000`                         |

### Database Configuration

The application uses SQLite by default for development. For production, consider PostgreSQL:

```env
DATABASE_URL=postgresql://username:password@localhost/fashcheck
```

## 🔐 Security Features

-   **Password Hashing**: bcrypt with salt for secure password storage
-   **JWT Authentication**: Stateless authentication with configurable expiration
-   **CORS Protection**: Configurable cross-origin resource sharing
-   **Input Validation**: Pydantic models for request validation
-   **SQL Injection Prevention**: SQLAlchemy ORM protection

## 🧪 Testing

### Manual Testing

1. **Health Check**

```bash
curl http://localhost:8000/health
```

2. **User Registration**

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","email":"test@example.com","password":"password123"}'
```

3. **User Login**

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","password":"password123"}'
```

4. **Protected Endpoint Access**

```bash
curl -X GET http://localhost:8000/users/profile \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

### Test Data

Create test users and sample data:

```python
# Run in Python console
from app.models import get_db
from app.auth import create_user

db = next(get_db())
user = create_user(db, "testuser", "test@example.com", "password123")
print(f"Created user: {user.username}")
```

## 🚀 Deployment

### Development

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Production

1. **Set production environment variables**
2. **Use production database (PostgreSQL recommended)**
3. **Enable HTTPS**
4. **Configure proper CORS origins**
5. **Set up reverse proxy (nginx)**
6. **Use production WSGI server (gunicorn)**

```bash
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

## 📊 Monitoring

### Health Checks

-   Endpoint: `GET /health`
-   Database connectivity check
-   OpenAI API availability check

### Logging

-   User activity tracking
-   Error logging with stack traces
-   Performance metrics

### Analytics

-   User engagement metrics
-   Fashion analysis statistics
-   System performance data

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Guidelines

-   Follow PEP 8 style guide
-   Add docstrings to functions and classes
-   Write tests for new features
-   Update documentation for API changes

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

### Common Issues

1. **OpenAI API Errors**

    - Verify API key is correct
    - Check API quota and billing
    - Ensure network connectivity

2. **Database Issues**

    - Check database file permissions
    - Verify database URL format
    - Run database initialization

3. **Authentication Problems**
    - Verify JWT secret key
    - Check token expiration
    - Validate user credentials

### Getting Help

-   **Documentation**: [API_DOCUMENTATION.md](API_DOCUMENTATION.md)
-   **Issues**: Create an issue in the repository
-   **Email**: [Your contact email]

## 🔄 Version History

### v1.0.0 (Current)

-   Initial release
-   Core fashion analysis features
-   User authentication system
-   Admin dashboard
-   Comprehensive API documentation

### Roadmap

-   [ ] Mobile application
-   [ ] Advanced ML models
-   [ ] Social features
-   [ ] Marketplace integration
-   [ ] Multi-language support

## 🙏 Acknowledgments

-   **OpenAI** for providing GPT-4 Vision capabilities
-   **FastAPI** team for the excellent web framework
-   **Bootstrap** for the responsive UI components
-   **SQLAlchemy** for robust database management

---

**Made with ❤️ for fashion enthusiasts**
