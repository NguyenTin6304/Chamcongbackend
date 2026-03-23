import argparse

from app.core.config import settings
from app.services.mail.base import ResetPasswordMail
from app.services.mail.factory import get_mail_sender


def main() -> None:
    parser = argparse.ArgumentParser(description="Test sending reset password email")
    parser.add_argument("--to", required=True, help="Recipient email")
    parser.add_argument(
        "--url",
        default=f"{settings.RESET_PASSWORD_URL_BASE}?token=demo-token",
        help="Reset password URL",
    )
    parser.add_argument("--token", default="demo-token", help="Reset token text")
    parser.add_argument("--expires", type=int, default=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)
    args = parser.parse_args()

    sender = get_mail_sender()
    sender.send_reset_password(
        ResetPasswordMail(
            to_email=args.to,
            reset_url=args.url,
            reset_token=args.token,
            expires_minutes=args.expires,
        )
    )
    print("Mail sender executed successfully")


if __name__ == "__main__":
    main()
