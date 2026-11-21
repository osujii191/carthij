import os
from typing import Optional
from dotenv import load_dotenv
from google import genai

load_dotenv()


def get_genai_client(
    api_key: Optional[str] = None,
    vertexai: Optional[bool] = None,
    project: Optional[str] = None,
    location: Optional[str] = None,
) -> genai.Client:
    """
    Initializes and returns a Google GenAI Client.
    
    Supports:
    1. Gemini Developer API: Set GEMINI_API_KEY (or pass api_key).
    2. Google Cloud Vertex AI: Set vertexai=True (or USE_VERTEXAI=true) along with
       GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION.
    """
    use_vertex = (
        vertexai
        if vertexai is not None
        else os.getenv("USE_VERTEXAI", "").lower() in ("true", "1", "yes")
    )

    if use_vertex:
        project_id = project or os.getenv("GOOGLE_CLOUD_PROJECT")
        region = location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        return genai.Client(vertexai=True, project=project_id, location=region)

    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        # Fall back to default credentials or environment lookup
        return genai.Client()
    return genai.Client(api_key=key)
