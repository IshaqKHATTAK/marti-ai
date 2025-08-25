from app.models.user import User, UserRole
from fastapi import HTTPException
from app.models.chatbot_model import ChatbotConfig
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

def validate_user_role(role: str) -> str:
    """Validate user role"""
    if role not in [r.value for r in UserRole]:
        raise ValueError(f"Invalid role: {role}")
    return role

async def toggle_user_status(user: User) -> User:
    """Toggle user active status"""
    user.is_active = not user.is_active
    if not user.is_verified:
        user.is_verified = True
    return user

async def toggle_chatbot_status(chatbot: ChatbotConfig) -> User:
    """Toggle user active status"""
    chatbot.memory_status = not chatbot.memory_status
    return chatbot

async def toggle_true_user_status(user: User) -> User:
    """Toggle user active status"""
    user.is_active = True
    return user

async def toggle_false_user_status(user: User) -> User:
    """Toggle user active status"""
    user.is_active = False
    return user

async def toggle_user_paid_status(user: User) -> User:
    """Toggle user paid status"""
    user.is_paid = not user.is_paid
    return user

def extract_domain_urls(root_url: str) -> list[str]:
    """
    """
    parsed_root = urlparse(root_url)
    root_domain = parsed_root.netloc.lower()

    try:
        resp = requests.get(root_url, timeout=1000)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not fetch {root_url!r}") from exc
 
    soup = BeautifulSoup(resp.text, "html.parser")

    urls = set()
    for tag in soup.select("a[href]"):
        href = tag["href"].strip()
        
        # Convert relative → absolute
        abs_url = urljoin(root_url, href)

        # Keep only links that belong to the same domain
        if urlparse(abs_url).netloc.lower() == root_domain:
            urls.add(abs_url)

    return sorted(urls)