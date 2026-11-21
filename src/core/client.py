import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from google import genai

# Always prefer the project root .env (avoids being shadowed by any nested src/.env)
_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
if _ROOT_ENV.exists():
    load_dotenv(dotenv_path=_ROOT_ENV)
else:
    load_dotenv()

DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")


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
