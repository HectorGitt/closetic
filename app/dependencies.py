import os
from fastapi import HTTPException, status
from fastapi.security import HTTPBearer
from openai import OpenAI
import base64
import io
from PIL import Image
from pydantic import BaseModel


# Turn to pydantic openai
class AnalysisData(BaseModel):
    overall_rating: int
    color_analysis: str
    texture_analysis: str
    fit_analysis: str
    improvements: str
    alternatives: str
    occasion: str
    trends: str


class RecommendationsData(BaseModel):
    immediate_improvements: list[str]
    shopping_list: list[str]
    styling_alternatives: list[str]
    color_palette: list[str]
    accessories: list[str]


class FashionAnalysisData(BaseModel):
    analysis: AnalysisData
    recommendations: RecommendationsData


class FashionAnalysisResponse(BaseModel):
    success: bool
    message: str
    data: FashionAnalysisData


# Security
security = HTTPBearer()


def get_openai_client():
    """Get OpenAI client instance"""
    api_key = os.getenv("OPENAI_API_KEY", default="")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OpenAI API key not configured",
        )
    return OpenAI(api_key=api_key)


def process_image(image_file) -> str:
    """Process uploaded image and convert to base64"""
    try:
        # Read image - FastAPI UploadFile has a .file attribute
        image = Image.open(image_file.file)

        # Convert to RGB if necessary
        if image.mode != "RGB":
            image = image.convert("RGB")

        # Resize image if too large (max 1024x1024 for OpenAI)
        max_size = 1024
        if max(image.size) > max_size:
            ratio = max_size / max(image.size)
            new_size = tuple(int(dim * ratio) for dim in image.size)
            image = image.resize(new_size, Image.Resampling.LANCZOS)

        # Convert to base64
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        image_base64 = base64.b64encode(buffer.getvalue()).decode()

        return f"data:image/jpeg;base64,{image_base64}"
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error processing image: {str(e)}",
        )


# def process_camera_frame(frame_data: str) -> str:
#     """Process camera frame data"""
#     try:
#         # Remove data URL prefix if present
#         if frame_data.startswith("data:image"):
#             frame_data = frame_data.split(",")[1]

#         # Decode base64
#         image_data = base64.b64decode(frame_data)

#         # Convert to PIL Image
#         image = Image.open(io.BytesIO(image_data))

#         # Convert to RGB if necessary
#         if image.mode != "RGB":
#             image = image.convert("RGB")

#         # Process with OpenCV for better analysis
#         cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

#         # Apply some basic image enhancement
#         cv_image = cv2.convertScaleAbs(cv_image, alpha=1.1, beta=10)

#         # Convert back to PIL
#         enhanced_image = Image.fromarray(cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB))

#         # Convert to base64
#         buffer = io.BytesIO()
#         enhanced_image.save(buffer, format="JPEG", quality=85)
#         image_base64 = base64.b64encode(buffer.getvalue()).decode()

#         return f"data:image/jpeg;base64,{image_base64}"
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail=f"Error processing camera frame: {str(e)}",
#         )


async def analyze_fashion_with_openai(
    client: OpenAI, image_base64: str, analysis_type: str = "comprehensive"
) -> dict:
    """Analyze fashion using OpenAI Vision API"""
    try:
        # Prepare prompt based on analysis type
        prompts = {
            "comprehensive": """
            Analyze this fashion outfit/dress in detail. Provide:
            1. Overall style assessment (rating 1-10)
            2. Color coordination analysis
            3. Texture and material observations
            4. Fit and silhouette evaluation
            5. Specific improvement suggestions
            6. Alternative styling recommendations
            7. Occasion appropriateness
            8. Trend relevance
            
            Format your response as JSON with these keys: overall_rating, color_analysis, texture_analysis, fit_analysis, improvements, alternatives, occasion, trends.
            json object should just be enclosed in curly braces, no backticks
            """,
            "color_match": """
            Focus specifically on color coordination in this outfit. Analyze:
            1. Color harmony and balance
            2. Complementary/contrasting elements
            3. Seasonal appropriateness of colors
            4. Suggestions for better color combinations
            
            Format as JSON with keys: harmony_score, balance_analysis, seasonal_fit, color_suggestions.
            """,
            "style_suggestions": """
            Provide styling suggestions for this outfit:
            1. How to improve the current look
            2. Accessories that would enhance it
            3. Different occasions it could work for
            4. Seasonal adaptations
            
            Format as JSON with keys: current_improvements, accessories, occasions, seasonal_tips.
            """,
        }

        prompt = prompts.get(analysis_type, prompts["comprehensive"])

        response = client.responses.parse(
            model="gpt-4o-mini",
            input=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": image_base64,
                        },
                    ],
                },
            ],
            text_format=FashionAnalysisResponse,
        )

        # Try to parse the structured output
        content = response.output_parsed
        try:
            # If content is already a FashionAnalysisResponse object, convert to dict
            if isinstance(content, FashionAnalysisResponse):
                return content.model_dump()
            else:
                # Try to parse as JSON if it's a string
                import json

                return json.loads(content)
        except Exception:
            # If parsing fails, return structured fallback
            return {
                "analysis_type": analysis_type,
                "raw_analysis": str(content),
                "success": True,
            }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error analyzing with OpenAI: {str(e)}",
        )
