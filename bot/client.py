import logging
from pyrogram import Client
from pytgcalls import GroupCallFactory

from config import API_ID, API_HASH, SESSION_STRING

logger = logging.getLogger(__name__)


def create_client() -> Client:
    """Initialize Pyrogram client."""
    app = Client(
        "vc_relay_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
    )
    return app
