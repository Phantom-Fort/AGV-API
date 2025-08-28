from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional
from pydantic import BaseModel
import os
import httpx
import re
import logging
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI(
    title="AGV TaskOn Verification API",
    description="An API for AGV TaskOn verification integration",
    version="1.0.0",
)

# Add CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class VerificationResponse(BaseModel):
    result: dict
    error: Optional[str] = None

# Google Sheets API setup
GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH")
AGENT_SHEET_ID = os.getenv("AGENT_SHEET_ID")
WALLET_SHEET_ID = os.getenv("WALLET_SHEET_ID")
AGENT_EMAIL_COLUMN = os.getenv("AGENT_EMAIL_COLUMN", "F")
WALLET_ADDRESS_COLUMN = os.getenv("WALLET_ADDRESS_COLUMN", "S")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

try:
    credentials = service_account.Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS_PATH, scopes=SCOPES
    )
    sheets_service = build("sheets", "v4", credentials=credentials)
except Exception as e:
    logger.error(f"Failed to initialize Google Sheets API: {str(e)}")
    sheets_service = None

def column_letter_to_index(letter: str) -> int:
    """Convert a column letter (e.g., 'F') to a 0-based index."""
    letter = letter.upper().strip()
    index = 0
    for char in letter:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1

async def get_value_in_column(sheet_id: str, column_letter: str, value: str) -> bool:
    """Direct GET check for a value in a specific column"""
    if not sheets_service:
        logger.error("Google Sheets service not initialized")
        return False
    if not sheet_id:
        logger.error("Sheet ID not configured")
        return False

    value_lower = value.strip().lower()
    try:
        # Get first sheet name
        sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheet_name = sheet_metadata["sheets"][0]["properties"]["title"]

        # Target range for just that column
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!{column_letter}:{column_letter}"
        ).execute()

        values = result.get("values", [])
        if not values:
            return False

        for row in values:
            if row and row[0].strip().lower() == value_lower:
                return True
        return False
    except HttpError as e:
        logger.error(f"Google Sheets API error: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error querying sheet: {str(e)}")
        raise

def extract_tweet_id_from_url(url: str):
    match = re.search(r"/status/(\d+)", url)
    return match.group(1) if match else None

# ---------------- Endpoints ---------------- #

@app.post("/verify-agent-application")
async def verify_agent_application(request: Request) -> VerificationResponse:
    body = await request.json()
    email = (body.get("email") or "").strip()
    if not email:
        return VerificationResponse(result={"isValid": False}, error="Missing email")

    try:
        found = await get_value_in_column(AGENT_SHEET_ID, AGENT_EMAIL_COLUMN, email)
        return VerificationResponse(result={"isValid": found, "details": {"email": email, "found": found}})
    except Exception as e:
        return VerificationResponse(result={"isValid": False}, error=f"Error: {str(e)}")

@app.post("/verify-content")
async def verify_content(request: Request) -> dict:
    body = await request.json()
    link = (body.get("link") or "").strip()
    if not link:
        return {"result": {"point": 0}, "error": "Missing 'link'"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(link)
            if r.status_code >= 400:
                return {"result": {"point": 0}, "error": f"Unreachable URL (status {r.status_code})"}
            text = r.text.lower()

        has_hashtags = all(tag in text for tag in ["#agv", "#tree", "#rwa"])
        has_sentence = "agv protocol" in text

        if has_hashtags and has_sentence:
            return {"result": {"point": 500}}
        else:
            return {"result": {"point": 0}}
    except Exception as e:
        return {"result": {"point": 0}, "error": f"Error: {str(e)}"}

@app.post("/verify-share-nft")
async def verify_share_nft(request: Request) -> dict:
    body = await request.json()
    tweet_url = (body.get("tweetUrl") or "").strip()
    if not tweet_url:
        return {"result": {"point": 0}, "error": "Missing 'tweetUrl'"}

    tweet_id = extract_tweet_id_from_url(tweet_url)
    if not tweet_id:
        return {"result": {"point": 0}, "error": "Invalid X post URL"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(tweet_url)
            if r.status_code >= 400:
                return {"result": {"point": 0}, "error": f"Unreachable (status {r.status_code})"}
            text = r.text.lower()

        has_hashtags = all(tag in text for tag in ["#agv", "#tree", "#rwa"])
        has_mention = "@agvprotocol" in text

        if has_hashtags and has_mention:
            return {"result": {"point": 300}}
        else:
            return {"result": {"point": 0}}
    except Exception as e:
        return {"result": {"point": 0}, "error": f"Error: {str(e)}"}

@app.get("/verify-wallet")
async def verify_wallet(address: str = None) -> VerificationResponse:
    if not address:
        return VerificationResponse(result={"isValid": False}, error="Missing 'address'")
    try:
        found = await get_value_in_column(WALLET_SHEET_ID, WALLET_ADDRESS_COLUMN, address)
        return VerificationResponse(result={"isValid": found, "details": {"wallet": address, "found": found}})
    except Exception as e:
        return VerificationResponse(result={"isValid": False}, error=f"Error: {str(e)}")

@app.get("/api/health")
async def health():
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": "Welcome to AGV TaskOn Verification API"}
