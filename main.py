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

# Add CORS middleware configuration
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
    """Convert a column letter (e.g., 'F', 'S') to a 0-based index."""
    try:
        letter = letter.upper().strip()
        if not letter or not all(c.isalpha() for c in letter):
            raise ValueError(f"Invalid column letter: {letter}")
        index = 0
        for char in letter:
            index = index * 26 + (ord(char) - ord('A') + 1)
        return index - 1
    except Exception as e:
        logger.error(f"Error converting column letter '{letter}' to index: {str(e)}")
        raise

def extract_tweet_id_from_url(url: str):
    match = re.search(r"/status/(\d+)", url)
    return match.group(1) if match else None

async def is_email_in_sheet(email: str, sheet_id: str, column_letter: str) -> bool:
    if not sheets_service:
        logger.error("Google Sheets service not initialized")
        return False
    if not sheet_id:
        logger.error("Sheet ID not configured")
        return False
    
    email_lower = email.strip().lower()
    try:
        # Get the first sheet's name
        sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheet_name = sheet_metadata["sheets"][0]["properties"]["title"]
        
        # Get all values in the sheet
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!A:ZZ"  # Broad range to cover all columns
        ).execute()
        values = result.get("values", [])
        if not values:
            logger.info("No data found in sheet")
            return False
        
        # Convert column letter to index
        try:
            column_index = column_letter_to_index(column_letter)
        except ValueError as e:
            logger.error(f"Invalid column letter '{column_letter}': {str(e)}")
            return False
        
        if column_index >= len(values[0]):
            logger.error(f"Column '{column_letter}' (index {column_index}) is out of range")
            return False
        
        # Check rows for the email (skip header row)
        for row in values[1:]:
            if len(row) > column_index and row[column_index].strip().lower() == email_lower:
                logger.info(f"Email {email_lower} found in sheet")
                return True
        logger.info(f"Email {email_lower} not found after checking {len(values)-1} rows")
        return False
    except HttpError as e:
        logger.error(f"Google Sheets API error: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error querying sheet: {str(e)}")
        raise

async def is_wallet_in_sheet(address: str, sheet_id: str, column_letter: str) -> bool:
    if not sheets_service:
        logger.error("Google Sheets service not initialized")
        return False
    if not sheet_id:
        logger.error("Sheet ID not configured")
        return False
    
    address_lower = address.strip().lower()
    try:
        # Get the first sheet's name
        sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheet_name = sheet_metadata["sheets"][0]["properties"]["title"]
        
        # Get all values in the sheet
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!A:ZZ"  # Broad range to cover all columns
        ).execute()
        values = result.get("values", [])
        if not values:
            logger.info("No data found in sheet")
            return False
        
        # Convert column letter to index
        try:
            column_index = column_letter_to_index(column_letter)
        except ValueError as e:
            logger.error(f"Invalid column letter '{column_letter}': {str(e)}")
            return False
        
        if column_index >= len(values[0]):
            logger.error(f"Column '{column_letter}' (index {column_index}) is out of range")
            return False
        
        # Check rows for the wallet address (skip header row)
        for row in values[1:]:
            if len(row) > column_index and row[column_index].strip().lower() == address_lower:
                logger.info(f"Wallet {address_lower} found in sheet")
                return True
        logger.info(f"Wallet {address_lower} not found after checking {len(values)-1} rows")
        return False
    except HttpError as e:
        logger.error(f"Google Sheets API error: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error querying sheet: {str(e)}")
        raise

@app.post("/verify-agent-application")
async def verify_agent_application(
    request: Request,
    authorization: Optional[str] = Header(None)
) -> VerificationResponse:
    try:
        body = await request.json()
    except:
        logger.error("Invalid JSON body")
        return VerificationResponse(result={"isValid": False}, error="Invalid JSON body")
    
    email = (body.get("email") or "").strip()
    if not email:
        logger.error("Missing email in request")
        return VerificationResponse(result={"isValid": False}, error="Missing email")
    if not sheets_service:
        logger.error("Google Sheets integration not configured")
        return VerificationResponse(result={"isValid": False}, error="Google Sheets integration not configured")
    if not AGENT_SHEET_ID:
        logger.error("Agent sheet ID not configured")
        return VerificationResponse(result={"isValid": False}, error="Agent sheet ID not configured")
    
    try:
        found = await is_email_in_sheet(email, AGENT_SHEET_ID, AGENT_EMAIL_COLUMN)
        logger.info(f"Email verification result for {email}: {found}")
        return VerificationResponse(result={"isValid": found, "details": {"email": email, "found": found}})
    except HttpError as e:
        logger.error(f"Google Sheets API error for email {email}: {str(e)}")
        return VerificationResponse(result={"isValid": False}, error=f"Google Sheets API error: {str(e)}")
    except Exception as e:
        logger.error(f"Error verifying email {email}: {str(e)}")
        return VerificationResponse(result={"isValid": False}, error=f"Error verifying email: {str(e)}")

@app.post("/verify-content")
async def verify_content(
    request: Request,
    authorization: Optional[str] = Header(None)
) -> VerificationResponse:
    try:
        body = await request.json()
    except:
        logger.error("Invalid JSON body")
        return VerificationResponse(result={"isValid": False}, error="Invalid JSON body")
    
    link = (body.get("link") or "").strip()
    if not link:
        logger.error("Missing link in request")
        return VerificationResponse(result={"isValid": False}, error="Missing 'link' in request body")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            logger.info(f"Fetching content from {link}")
            r = await client.get(link)
            if r.status_code >= 400:
                logger.warning(f"Unreachable URL {link}: Status {r.status_code}")
                return VerificationResponse(result={"isValid": False, "details": {"reason": "unreachable", "status_code": r.status_code}})
            text = r.text.lower()

        hasAGV = "agv" in text
        hasTREE = "tree" in text
        hasRWA = "rwa" in text
        hasTag = "@agvprotocol" in text

        is_valid = hasAGV and hasTREE and hasRWA and hasTag
        details = {"hasAGV": hasAGV, "hasTREE": hasTREE, "hasRWA": hasRWA, "hasTag": hasTag}
        logger.info(f"Content verification result: {is_valid}, details: {details}")
        return VerificationResponse(result={"isValid": is_valid, "details": details})
    except Exception as e:
        logger.error(f"Content fetch error: {str(e)}")
        return VerificationResponse(result={"isValid": False}, error=f"Fetch error: {str(e)}")

@app.post("/verify-share-nft")
async def verify_share_nft(
    request: Request,
    authorization: Optional[str] = Header(None)
) -> VerificationResponse:
    try:
        body = await request.json()
    except:
        logger.error("Invalid JSON body")
        return VerificationResponse(result={"isValid": False}, error="Invalid JSON body")
    
    tweet_url = (body.get("tweetUrl") or "").strip()
    if not tweet_url:
        logger.error("Missing tweetUrl in request")
        return VerificationResponse(result={"isValid": False}, error="Missing 'tweetUrl' in request body")

    tweet_id = extract_tweet_id_from_url(tweet_url)
    if not tweet_id:
        logger.error(f"Invalid X post URL: {tweet_url}")
        return VerificationResponse(result={"isValid": False}, error="Invalid X post URL")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            logger.info(f"Fetching X post from {tweet_url}")
            r = await client.get(tweet_url)
            if r.status_code >= 400:
                logger.warning(f"Unreachable X post {tweet_url}: Status {r.status_code}")
                return VerificationResponse(result={"isValid": False, "details": {"reason": "unreachable", "status_code": r.status_code}})
            text = r.text.lower()

        has_hashtags = all(tag in text for tag in ["#agv", "#tree", "#rwa"])
        has_mention = "@agvprotocol" in text
        has_media = has_hashtags and has_mention  # Fallback assumption for scraping

        is_valid = has_hashtags and has_mention and has_media
        details = {"has_hashtags": has_hashtags, "has_mention": has_mention, "has_media": has_media}
        logger.info(f"Share NFT verification result: {is_valid}, details: {details}")
        return VerificationResponse(result={"isValid": is_valid, "details": details})
    except Exception as e:
        logger.error(f"X post verification error: {str(e)}")
        return VerificationResponse(result={"isValid": False}, error=f"Verification error: {str(e)}")

@app.get("/verify-wallet")
async def verify_wallet(
    address: str = None,
    authorization: Optional[str] = Header(None)
) -> VerificationResponse:
    if not address:
        logger.error("Missing address parameter")
        return VerificationResponse(result={"isValid": False}, error="Missing 'address' parameter")
    if not sheets_service:
        logger.error("Google Sheets integration not configured")
        return VerificationResponse(result={"isValid": False}, error="Google Sheets integration not configured")
    if not WALLET_SHEET_ID:
        logger.error("Wallet sheet ID not configured")
        return VerificationResponse(result={"isValid": False}, error="Wallet sheet ID not configured")
    
    try:
        found = await is_wallet_in_sheet(address, WALLET_SHEET_ID, WALLET_ADDRESS_COLUMN)
        logger.info(f"Wallet verification result for {address}: {found}")
        return VerificationResponse(result={"isValid": found, "details": {"wallet": address, "found": found}})
    except HttpError as e:
        logger.error(f"Google Sheets API error for wallet {address}: {str(e)}")
        return VerificationResponse(result={"isValid": False}, error=f"Google Sheets API error: {str(e)}")
    except Exception as e:
        logger.error(f"Error verifying wallet {address}: {str(e)}")
        return VerificationResponse(result={"isValid": False}, error=f"Error verifying wallet: {str(e)}")

@app.get("/api/health")
async def health():
    logger.info("Health check requested")
    return {"status": "ok"}

@app.get("/")
async def root():
    logger.info("Root endpoint requested")
    return {"message": "Welcome to AGV TaskOn Verification API"}