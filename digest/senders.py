"""Email senders for weekly digest.

Supports:
- DemoSender: Prints email details without sending (for testing)
- SendGridSender: Sends via SendGrid API
- GmailSender: Sends via Gmail SMTP (requires app password)
"""

import logging
import os
import smtplib
from abc import ABC, abstractmethod
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.policy import EmailPolicy
from typing import List, Optional

# Email policy for proper UTF-8 encoding
UTF8_POLICY = EmailPolicy(utf8=True)

logger = logging.getLogger(__name__)


class EmailSender(ABC):
    """Abstract base class for email senders."""

    @abstractmethod
    def send(
        self,
        to: List[str],
        subject: str,
        html_content: str,
        text_content: str,
    ) -> dict:
        """Send an email.

        Args:
            to: List of recipient email addresses
            subject: Email subject line
            html_content: HTML body content
            text_content: Plain text body content

        Returns:
            Dictionary with:
            - success: bool
            - message: str (success message or error details)
            - details: dict (optional additional info)
        """
        pass


class DemoSender(EmailSender):
    """Demo sender that prints email details without actually sending."""

    def __init__(self, output_path: Optional[str] = None):
        """Initialize demo sender.

        Args:
            output_path: Optional path to write demo output
        """
        self.output_path = output_path

    def send(
        self,
        to: List[str],
        subject: str,
        html_content: str,
        text_content: str,
    ) -> dict:
        """Print email details without sending.

        Returns:
            Success result with demo details
        """
        output_lines = [
            "",
            "=" * 70,
            "DEMO EMAIL SEND",
            "=" * 70,
            f"To: {', '.join(to)}",
            f"Subject: {subject}",
            f"HTML Content Length: {len(html_content)} chars",
            f"Text Content Length: {len(text_content)} chars",
            "=" * 70,
            "",
            "First 500 chars of text content:",
            "-" * 40,
            text_content[:500] + "..." if len(text_content) > 500 else text_content,
            "-" * 40,
            "",
        ]

        output = "\n".join(output_lines)
        print(output)

        if self.output_path:
            with open(self.output_path, "w", encoding="utf-8") as f:
                f.write(output)
            logger.info("Demo output written to: %s", self.output_path)

        return {
            "success": True,
            "message": "Demo send completed (no email actually sent)",
            "details": {
                "recipients": to,
                "subject": subject,
                "html_length": len(html_content),
                "text_length": len(text_content),
            },
        }


class SendGridSender(EmailSender):
    """Send emails via SendGrid API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        from_email: Optional[str] = None,
        from_name: Optional[str] = "SpotItEarly",
    ):
        """Initialize SendGrid sender.

        Args:
            api_key: SendGrid API key (defaults to SENDGRID_API_KEY env var)
            from_email: Sender email address (defaults to FROM_EMAIL env var)
            from_name: Sender display name
        """
        self.api_key = api_key or os.environ.get("SENDGRID_API_KEY")
        self.from_email = from_email or os.environ.get("FROM_EMAIL")
        self.from_name = from_name

        if not self.api_key:
            raise ValueError(
                "SendGrid API key not configured. "
                "Set SENDGRID_API_KEY environment variable or pass api_key parameter."
            )

        if not self.from_email:
            raise ValueError(
                "From email not configured. "
                "Set FROM_EMAIL environment variable or pass from_email parameter."
            )

    def send(
        self,
        to: List[str],
        subject: str,
        html_content: str,
        text_content: str,
    ) -> dict:
        """Send email via SendGrid.

        Returns:
            Result dictionary with success status and details
        """
        try:
            # Import sendgrid here to avoid requiring it unless actually used
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import (
                Mail, Email, To, Content, Personalization
            )

            # Create message
            message = Mail()
            message.from_email = Email(self.from_email, self.from_name)
            message.subject = subject

            # Add recipients
            personalization = Personalization()
            for recipient in to:
                personalization.add_to(To(recipient))
            message.add_personalization(personalization)

            # Add content (text first, then HTML)
            message.add_content(Content("text/plain", text_content))
            message.add_content(Content("text/html", html_content))

            # Send
            sg = SendGridAPIClient(self.api_key)
            response = sg.send(message)

            logger.info(
                "SendGrid email sent: status=%d, recipients=%s",
                response.status_code,
                to,
            )

            return {
                "success": response.status_code in (200, 201, 202),
                "message": f"Email sent successfully (status: {response.status_code})",
                "details": {
                    "status_code": response.status_code,
                    "recipients": to,
                    "subject": subject,
                },
            }

        except ImportError:
            return {
                "success": False,
                "message": "SendGrid library not installed. Run: pip install sendgrid",
                "details": {},
            }

        except Exception as e:
            logger.error("SendGrid send failed: %s", e)
            return {
                "success": False,
                "message": f"SendGrid send failed: {str(e)}",
                "details": {
                    "error": str(e),
                    "recipients": to,
                },
            }


class GmailSender(EmailSender):
    """Send emails via Gmail SMTP.

    Requires:
    - GMAIL_ADDRESS: Your Gmail address
    - GMAIL_APP_PASSWORD: App-specific password (NOT your regular password)

    To get an app password:
    1. Go to https://myaccount.google.com/security
    2. Enable 2-Step Verification if not already enabled
    3. Go to App passwords (https://myaccount.google.com/apppasswords)
    4. Generate a new app password for "Mail"
    5. Use the 16-character password (without spaces)
    """

    def __init__(
        self,
        gmail_address: Optional[str] = None,
        app_password: Optional[str] = None,
        from_name: Optional[str] = "SpotItEarly",
    ):
        """Initialize Gmail sender.

        Args:
            gmail_address: Gmail address (defaults to GMAIL_ADDRESS env var)
            app_password: Gmail app password (defaults to GMAIL_APP_PASSWORD env var)
            from_name: Sender display name
        """
        self.gmail_address = gmail_address or os.environ.get("GMAIL_ADDRESS")
        self.app_password = app_password or os.environ.get("GMAIL_APP_PASSWORD")
        self.from_name = from_name

        if not self.gmail_address:
            raise ValueError(
                "Gmail address not configured. "
                "Set GMAIL_ADDRESS environment variable or pass gmail_address parameter."
            )

        if not self.app_password:
            raise ValueError(
                "Gmail app password not configured. "
                "Set GMAIL_APP_PASSWORD environment variable or pass app_password parameter. "
                "See https://myaccount.google.com/apppasswords to generate one."
            )

    def send(
        self,
        to: List[str],
        subject: str,
        html_content: str,
        text_content: str,
    ) -> dict:
        """Send email via Gmail SMTP.

        Returns:
            Result dictionary with success status and details
        """
        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.gmail_address}>"
            msg["To"] = ", ".join(to)

            # Attach text and HTML parts (text first, HTML second for proper rendering)
            text_part = MIMEText(text_content, "plain", "utf-8")
            html_part = MIMEText(html_content, "html", "utf-8")
            msg.attach(text_part)
            msg.attach(html_part)

            # Connect to Gmail SMTP and send
            # Use as_bytes() with UTF-8 policy to properly handle Unicode content
            email_bytes = msg.as_bytes(policy=UTF8_POLICY)

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.gmail_address, self.app_password)
                server.sendmail(self.gmail_address, to, email_bytes)

            logger.info(
                "Gmail email sent: recipients=%s, subject=%s",
                to,
                subject,
            )

            return {
                "success": True,
                "message": f"Email sent successfully via Gmail to {len(to)} recipient(s)",
                "details": {
                    "recipients": to,
                    "subject": subject,
                    "from": self.gmail_address,
                },
            }

        except smtplib.SMTPAuthenticationError as e:
            logger.error("Gmail authentication failed: %s", e)
            return {
                "success": False,
                "message": (
                    "Gmail authentication failed. Make sure you're using an app password, "
                    "not your regular password. See https://myaccount.google.com/apppasswords"
                ),
                "details": {"error": str(e)},
            }

        except Exception as e:
            logger.error("Gmail send failed: %s", e)
            return {
                "success": False,
                "message": f"Gmail send failed: {str(e)}",
                "details": {
                    "error": str(e),
                    "recipients": to,
                },
            }


def get_sender(
    send_mode: str = "demo",
    api_key: Optional[str] = None,
    from_email: Optional[str] = None,
    gmail_address: Optional[str] = None,
    gmail_app_password: Optional[str] = None,
    output_path: Optional[str] = None,
) -> EmailSender:
    """Factory function to get appropriate email sender.

    Args:
        send_mode: "demo", "sendgrid", or "gmail"
        api_key: SendGrid API key (for sendgrid mode)
        from_email: Sender email (for sendgrid mode)
        gmail_address: Gmail address (for gmail mode)
        gmail_app_password: Gmail app password (for gmail mode)
        output_path: Path for demo output (for demo mode)

    Returns:
        EmailSender instance

    Raises:
        ValueError: If selected mode is not configured
    """
    if send_mode == "demo":
        return DemoSender(output_path=output_path)

    elif send_mode == "sendgrid":
        return SendGridSender(
            api_key=api_key,
            from_email=from_email,
        )

    elif send_mode == "gmail":
        return GmailSender(
            gmail_address=gmail_address,
            app_password=gmail_app_password,
        )

    else:
        raise ValueError(f"Unknown send mode: {send_mode}. Use 'demo', 'sendgrid', or 'gmail'.")


def validate_sendgrid_config() -> dict:
    """Validate SendGrid configuration.

    Returns:
        Dictionary with validation results
    """
    api_key = os.environ.get("SENDGRID_API_KEY")
    from_email = os.environ.get("FROM_EMAIL")

    errors = []

    if not api_key:
        errors.append("SENDGRID_API_KEY environment variable not set")

    if not from_email:
        errors.append("FROM_EMAIL environment variable not set")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "api_key_set": bool(api_key),
        "from_email_set": bool(from_email),
        "from_email": from_email if from_email else None,
    }


def validate_gmail_config() -> dict:
    """Validate Gmail SMTP configuration.

    Returns:
        Dictionary with validation results
    """
    gmail_address = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")

    errors = []

    if not gmail_address:
        errors.append("GMAIL_ADDRESS environment variable not set")

    if not app_password:
        errors.append("GMAIL_APP_PASSWORD environment variable not set")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "gmail_address_set": bool(gmail_address),
        "app_password_set": bool(app_password),
        "gmail_address": gmail_address if gmail_address else None,
    }
