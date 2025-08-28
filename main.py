from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional
from pydantic import BaseModel
import os
import json
import re
import logging
from urllib.parse import unquote
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv
import asyncio
import httpx
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# Get port from environment variable, default to 8000 for local testing
port = int(os.getenv("PORT", 8000))

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
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")
AGENT_SHEET_ID = os.getenv("AGENT_SHEET_ID")
WALLET_SHEET_ID = os.getenv("WALLET_SHEET_ID")
AGENT_EMAIL_COLUMN = os.getenv("AGENT_EMAIL_COLUMN", "F")
WALLET_ADDRESS_COLUMN = os.getenv("WALLET_ADDRESS_COLUMN", "S")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

try:
    if GOOGLE_CREDENTIALS:
        credentials_info = json.loads(GOOGLE_CREDENTIALS)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info, scopes=SCOPES
        )
    else:
        credentials = service_account.Credentials.from_service_account_file(
            os.getenv("GOOGLE_CREDENTIALS_PATH", "./creds.json"), scopes=SCOPES
        )
    sheets_service = build("sheets", "v4", credentials=credentials)
except Exception as e:
    logger.error(f"Failed to initialize Google Sheets API: {str(e)}")
    sheets_service = None

def column_letter_to_index(letter: str) -> int:
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
        sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheet_name = sheet_metadata["sheets"][0]["properties"]["title"]
        
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!{column_letter}:{column_letter}"
        ).execute()
        values = result.get("values", [])
        if not values:
            logger.info("No data found in sheet")
            return False
        
        for row in values[1:]:
            if row and row[0].strip().lower() == email_lower:
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
        sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheet_name = sheet_metadata["sheets"][0]["properties"]["title"]
        
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!{column_letter}:{column_letter}"
        ).execute()
        values = result.get("values", [])
        if not values:
            logger.info("No data found in sheet")
            return False
        
        for row in values[1:]:
            if row and row[0].strip().lower() == address_lower:
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

async def get_page_content(url: str) -> str:
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}) as client:
        try:
            response = await client.get(url)
            if response.status_code != 200:
                logger.error(f"Failed to fetch {url}, status code: {response.status_code}")
                return ""
            soup = BeautifulSoup(response.text, "lxml")
            # For X.com tweets, extract the tweet text
            tweet_div = soup.find("div", {"data-testid": "tweetText"})
            if tweet_div:
                return tweet_div.get_text().lower()
            # For general content (e.g., blog posts), extract all text
            return soup.get_text().lower() if soup else ""
        except Exception as e:
            logger.error(f"Error fetching content from {url}: {str(e)}")
            return ""

@app.get("/verify-agent-application")
async def verify_agent_application(address: str):
    if not address:
        logger.error("Missing address parameter")
        return VerificationResponse(result={"isValid": False}, error="Missing 'address' parameter")
    if not sheets_service:
        logger.error("Google Sheets integration not configured")
        return VerificationResponse(result={"isValid": False}, error="Google Sheets integration not configured")
    if not AGENT_SHEET_ID:
        logger.error("Agent sheet ID not configured")
        return VerificationResponse(result={"isValid": False}, error="Agent sheet ID not configured")
    
    try:
        address = unquote(address)
        found = await is_email_in_sheet(address, AGENT_SHEET_ID, AGENT_EMAIL_COLUMN)
        logger.info(f"Agent application verification result for {address}: {found}")
        return VerificationResponse(result={"isValid": found, "details": {"address": address, "found": found}})
    except HttpError as e:
        logger.error(f"Google Sheets API error for address {address}: {str(e)}")
        return VerificationResponse(result={"isValid": False}, error=f"Google Sheets API error: {str(e)}")
    except Exception as e:
        logger.error(f"Error verifying address {address}: {str(e)}")
        return VerificationResponse(result={"isValid": False}, error=f"Error verifying address: {str(e)}")

@app.get("/verify-content")
async def verify_content(data: str):
    if not data:
        logger.error("Missing data parameter")
        return VerificationResponse(result={"point": 0}, error="Missing 'data' parameter")
    
    try:
        data = unquote(data)
        content = await get_page_content(data)
        if not content:
            logger.error(f"No content fetched from {data}")
            return VerificationResponse(result={"point": 0}, error="Failed to fetch content")
        
        has_agv = "agv" in content
        has_protocol = "protocol" in content
        
        is_valid = has_agv and has_protocol
        logger.info(f"Content verification result: {is_valid}, has_agv={has_agv}, has_protocol={has_protocol}")
        
        return VerificationResponse(result={"point": 500 if is_valid else 0})
    except Exception as e:
        logger.error(f"Content verification error for {data}: {str(e)}")
        return VerificationResponse(result={"point": 0}, error=f"Verification error: {str(e)}")

@app.get("/verify-share-nft")
async def verify_share_nft(data: str, user_id: str = None):
    if not data:
        logger.error("Missing data parameter")
        return VerificationResponse(result={"point": 0}, error="Missing 'data' parameter")
    
    try:
        data = unquote(data)
        content = await get_page_content(data)
        if not content:
            logger.error(f"No content fetched from {data}")
            return VerificationResponse(result={"point": 0}, error="Failed to fetch content")
        
        has_agv = "#agv" in content
        has_nft = "nft" in content
        
        is_valid = has_agv and has_nft
        logger.info(f"Share NFT verification result: {is_valid}, has_agv={has_agv}, has_nft={has_nft}, user_id={user_id}")
        
        return VerificationResponse(result={"point": 300 if is_valid else 0})
    except Exception as e:
        logger.error(f"Share NFT verification error for {data}: {str(e)}")
        return VerificationResponse(result={"point": 0}, error=f"Verification error: {str(e)}")

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