import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlencode

from app.database import settings


class EmailConfigurationError(RuntimeError):
    """Raised when required SMTP settings have not been provided."""


def _smtp_sender() -> str:
    sender = settings.smtp_sender_email or settings.smtp_username
    if not settings.smtp_host or not sender:
        raise EmailConfigurationError(
            "SMTP_HOST and SMTP_SENDER_EMAIL (or SMTP_USERNAME) must be configured"
        )
    return sender


def build_password_reset_url(token: str) -> str:
    separator = "&" if "?" in settings.frontend_reset_url else "?"
    return f"{settings.frontend_reset_url}{separator}{urlencode({'token': token})}"


def send_password_reset_email(recipient: str, full_name: str, token: str) -> None:
    """Send a password-reset link through configured SMTP infrastructure."""
    sender = _smtp_sender()
    reset_url = build_password_reset_url(token)

    message = EmailMessage()
    message["Subject"] = "Reset your SubscriptHQ password"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        f"Hello {full_name},\n\n"
        "We received a request to reset your SubscriptHQ password. "
        f"Use this link within {settings.password_reset_expire_minutes} minutes:\n\n"
        f"{reset_url}\n\n"
        "If you did not request this change, you can ignore this email."
    )

    context = ssl.create_default_context()
    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
            context=context,
        ) as smtp:
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(
        settings.smtp_host,
        settings.smtp_port,
        timeout=settings.smtp_timeout_seconds,
    ) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls(context=context)
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


def send_invoice_email(recipient: str, customer_name: str, invoice) -> None:
    sender = _smtp_sender()
    message = EmailMessage()
    message["Subject"] = f"SubscriptHQ invoice {invoice.invoice_number}"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        f"Hello {customer_name},\n\nInvoice {invoice.invoice_number} is ready.\n"
        f"Total: {invoice.currency} {invoice.total_amount}\n"
        f"Amount due: {invoice.currency} {invoice.amount_due}\n"
        f"Due date: {invoice.due_date}\n\nPlease sign in to SubscriptHQ to view the invoice."
    )
    context = ssl.create_default_context()
    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds, context=context) as smtp:
            if settings.smtp_username and settings.smtp_password: smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
        return
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds) as smtp:
        if settings.smtp_use_tls: smtp.starttls(context=context)
        if settings.smtp_username and settings.smtp_password: smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)
