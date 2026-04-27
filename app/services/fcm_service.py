import logging

logger = logging.getLogger(__name__)

_initialized = False


def _ensure_app() -> bool:
    """Lazily initialize Firebase Admin SDK. Returns True when ready."""
    global _initialized
    if _initialized:
        return True

    from app.core.config import settings

    if not settings.FCM_ENABLED:
        return False
    if not settings.FCM_SERVICE_ACCOUNT_PATH:
        logger.warning("FCM_ENABLED is true but FCM_SERVICE_ACCOUNT_PATH is not set")
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials

        if not firebase_admin._apps:
            cred = credentials.Certificate(settings.FCM_SERVICE_ACCOUNT_PATH)
            firebase_admin.initialize_app(cred)

        _initialized = True
        return True
    except Exception:
        logger.exception("Failed to initialize Firebase Admin SDK")
        return False


def send_push_notification(
    fcm_token: str,
    title: str,
    body: str,
    data: dict[str, str] | None = None,
) -> bool:
    """Send a push notification to a single device token.

    Returns True on success, False when FCM is disabled or on any error.
    Never raises — failures are logged and swallowed so the caller's flow
    (email notification, API response) is not disrupted.
    """
    if not fcm_token or not fcm_token.strip():
        return False

    if not _ensure_app():
        return False

    try:
        from firebase_admin import messaging

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=data or {},
            token=fcm_token.strip(),
        )
        messaging.send(message)
        logger.debug("FCM push sent. title=%r token=%s…", title, fcm_token[:8])
        return True
    except Exception:
        logger.exception(
            "FCM push failed. title=%r token=%s…", title, fcm_token[:8]
        )
        return False
