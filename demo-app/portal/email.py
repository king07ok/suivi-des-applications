import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage


@dataclass(frozen=True)
class EmailSettings:
    host: str
    port: int
    username: str
    password: str
    sender: str
    use_starttls: bool
    portal_url: str

    @property
    def enabled(self) -> bool:
        return bool(self.host)

    @classmethod
    def from_env(cls) -> "EmailSettings":
        host = os.getenv("SMTP_HOST", "").strip()
        username = os.getenv("SMTP_USERNAME", "").strip()
        password = os.getenv("SMTP_PASSWORD", "")
        sender = os.getenv("SMTP_FROM", "").strip()
        try:
            port = int(os.getenv("SMTP_PORT", "587"))
        except ValueError as error:
            raise RuntimeError("SMTP_PORT doit être un nombre entre 1 et 65535.") from error
        if not 1 <= port <= 65535:
            raise RuntimeError("SMTP_PORT doit être un nombre entre 1 et 65535.")

        starttls_value = os.getenv("SMTP_USE_STARTTLS", "true").strip().lower()
        if starttls_value not in {"true", "false"}:
            raise RuntimeError("SMTP_USE_STARTTLS doit valoir true ou false.")
        if host and (not sender or "@" not in sender):
            raise RuntimeError("SMTP_FROM doit être une adresse e-mail valide.")
        if bool(username) != bool(password):
            raise RuntimeError("SMTP_USERNAME et SMTP_PASSWORD doivent être définis ensemble.")
        if username and starttls_value != "true":
            raise RuntimeError(
                "SMTP_USE_STARTTLS doit valoir true lorsque SMTP utilise des identifiants."
            )

        return cls(
            host=host,
            port=port,
            username=username,
            password=password,
            sender=sender,
            use_starttls=starttls_value == "true",
            portal_url=os.getenv("PORTAL_PUBLIC_URL", "http://localhost:8080").rstrip("/"),
        )


def send_email(
    settings: EmailSettings,
    recipient: str,
    subject: str,
    body: str,
) -> None:
    if not settings.enabled:
        raise RuntimeError("L’envoi d’e-mails est désactivé : SMTP_HOST n’est pas configuré.")

    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(settings.host, settings.port, timeout=10) as server:
        if settings.use_starttls:
            server.starttls(context=ssl.create_default_context())
        if settings.username:
            server.login(settings.username, settings.password)
        server.send_message(message)
