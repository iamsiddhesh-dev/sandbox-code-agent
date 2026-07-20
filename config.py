import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
E2B_API_KEY = os.getenv("E2B_API_KEY")
SANDBOX_BACKEND = os.getenv("SANDBOX_BACKEND", "e2b")
CODEGEN_MODEL = os.getenv("CODEGEN_MODEL", "haiku-4.5")

if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY not set in .env")
