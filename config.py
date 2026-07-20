import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
E2B_API_KEY = os.getenv("E2B_API_KEY")
SANDBOX_BACKEND = os.getenv("SANDBOX_BACKEND", "e2b")
CODEGEN_MODEL = os.getenv("CODEGEN_MODEL", "llama-3.3-70b-versatile")

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY not set in .env")
