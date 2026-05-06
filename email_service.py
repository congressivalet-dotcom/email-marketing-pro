"""
Servizio per l'invio delle email con tracking integrato.

Tutti i link cliccabili vengono riscritti per passare attraverso /track/click/{lid}
e viene iniettato un pixel 1x1 invisibile per tracciare l'apertura.

I link vengono salvati nel database (non più su file JSON) per essere persistenti
anche su filesystem effimeri come Render.
"""
import os
import re
import uuid
import logging
import smtplib
import mimetypes
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv

from database import SessionLocal
from models import TrackingLink

load_dotenv()

PROVIDER = os.getenv("EMAIL_PROVIDER", "dry_run")
APP_BASE = os.getenv("APP_BASE_URL", "http://localhost:8000")

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Email Marketing Pro")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USER)

logger = logging.getLogger(__name__)


def _save_tracking_link(link_id: str, url: str, campaign_id: int = None, email: str = ""):
    """Salva un link tracciato nel database."""
    db = SessionLocal()
    try:
        link = TrackingLink(link_id=link_id, url=url, campaign_id=campaign_id, email=email)
        db.add(link)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Errore salvataggio tracking link: {e}")
    finally:
        db.close()


def inject_tracking(html: str, track_id: str, campaign_id: int = None, recipient_email: str = "") -> str:
    """
    Inietta:
    - un pixel di tracciamento per registrare l'apertura
    - sostituisce ogni href con un link che passa per /track/click

    Funziona anche se l'HTML non contiene <body>.
    """
    pixel = (
        f'<img src="{APP_BASE}/track/open/{track_id}?email={urllib.parse.quote(recipient_email, safe="")}'
        f'{"&campaign_id=" + str(campaign_id) if campaign_id else ""}" '
        f'width="1" height="1" alt="" style="display:none;border:0;outline:none;">'
    )
    if "</body>" in html.lower():
        # case-insensitive replace su </body>
        html = re.sub(r"</body>", pixel + "</body>", html, count=1, flags=re.IGNORECASE)
    else:
        # niente </body> -> appendi alla fine
        html = html + pixel

    def repl(m):
        full_tag = m.group(0)
        url = m.group(1)
        # non tracciare link interni o non http
        if not url.startswith(("http://", "https://")):
            return full_tag
        # non tracciare il link di unsubscribe
        if "/unsubscribe" in url:
            return full_tag
        lid = uuid.uuid4().hex[:10]
        _save_tracking_link(lid, url, campaign_id=campaign_id, email=recipient_email)
        new_url = (
            f'{APP_BASE}/track/click/{lid}?email={urllib.parse.quote(recipient_email, safe="")}'
            f'{"&campaign_id=" + str(campaign_id) if campaign_id else ""}'
        )
        return full_tag.replace(url, new_url)

    return re.sub(r'<a\s+[^>]*href=["\'](.*?)["\']', repl, html, flags=re.IGNORECASE)


def _add_unsubscribe_footer(html: str, recipient_email: str) -> str:
    """Aggiunge il footer di unsubscribe (bilingue IT/EN) se non già presente."""
    if "unsubscribe" in html.lower():
        return html
    unsub_link = f"{APP_BASE}/unsubscribe?email={urllib.parse.quote(recipient_email, safe='')}"
    footer = (
        '<div style="margin-top:32px;padding-top:16px;border-top:1px solid #e2e8f0;'
        'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;'
        'font-size:12px;color:#64748b;text-align:center;line-height:1.6;">'
        f'<p style="margin:0 0 6px;">Hai ricevuto questa email perché sei iscritto alla nostra mailing list. '
        f'Se non desideri più ricevere queste comunicazioni, '
        f'<a href="{unsub_link}" style="color:#4f46e5;text-decoration:underline;">'
        f'clicca qui per disiscriverti</a>.</p>'
        f'<p style="margin:0;color:#94a3b8;">You received this email because you are subscribed to our mailing list. '
        f'If you no longer wish to receive these communications, '
        f'<a href="{unsub_link}" style="color:#4f46e5;text-decoration:underline;">'
        f'click here to unsubscribe</a>.</p>'
        '</div>'
    )
    if "</body>" in html.lower():
        return re.sub(r"</body>", footer + "</body>", html, count=1, flags=re.IGNORECASE)
    return html + footer


def send_email(
    to: str,
    subject: str,
    html: str,
    track: bool = True,
    attachment_path: str = None,
    attachment_paths: list = None,
    campaign_id: int = None,
    track_id: str = None,
) -> tuple:
    """
    Invia un'email. Restituisce (success: bool, track_id: str).

    - track_id: se fornito viene usato, altrimenti generato; consente di legare
      l'evento di apertura al CampaignLog.
    - attachment_path: singolo file (legacy, mantenuto per retrocompatibilità).
    - attachment_paths: lista di file. Se fornita, ha precedenza su attachment_path.
    """
    if track_id is None:
        track_id = uuid.uuid4().hex

    html = _add_unsubscribe_footer(html, to)

    if track:
        html = inject_tracking(html, track_id, campaign_id=campaign_id, recipient_email=to)

    msg = MIMEMultipart("mixed")
    from_addr = SMTP_FROM_EMAIL if PROVIDER == "smtp" and SMTP_FROM_EMAIL else "noreply@example.com"
    msg["From"] = f"{SMTP_FROM_NAME} <{from_addr}>"
    msg["To"] = to
    msg["Subject"] = subject

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)

    # Costruisce la lista finale di allegati da aggiungere
    paths_to_attach = []
    if attachment_paths:
        paths_to_attach.extend([p for p in attachment_paths if p])
    elif attachment_path:
        paths_to_attach.append(attachment_path)

    # Rimuovi duplicati mantenendo l'ordine
    seen = set()
    unique_paths = []
    for p in paths_to_attach:
        if p and p not in seen and os.path.exists(p):
            seen.add(p)
            unique_paths.append(p)

    for path in unique_paths:
        try:
            ctype, encoding = mimetypes.guess_type(path)
            if ctype is None or encoding is not None:
                ctype = "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            with open(path, "rb") as fp:
                file_part = MIMEBase(maintype, subtype)
                file_part.set_payload(fp.read())
            encoders.encode_base64(file_part)
            filename = os.path.basename(path)
            # Pulisce il prefisso UUID dai filename salvati con uuid
            if "_" in filename and len(filename.split("_")[0]) == 8:
                filename = "_".join(filename.split("_")[1:])
            elif filename.startswith("tpl_") and "_" in filename[4:]:
                # Rimuove prefisso tpl_xxxxxxxx_
                parts = filename.split("_", 2)
                if len(parts) >= 3:
                    filename = parts[2]
            file_part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(file_part)
            logger.info(f"📎 Allegato aggiunto: {filename}")
        except Exception as e:
            logger.error(f"❌ Errore allegato {path}: {e}")

    if PROVIDER == "dry_run":
        from pathlib import Path
        test_dir = Path(os.getenv("TEST_EMAILS_DIR", "test_emails"))
        test_dir.mkdir(exist_ok=True)
        safe = "".join(c for c in to if c.isalnum() or c in "@._-")
        path = test_dir / f"{safe}_{subject[:15]}.html"
        path.write_text(html, encoding="utf-8")
        return True, track_id

    if PROVIDER == "smtp":
        if not SMTP_USER or not SMTP_PASSWORD:
            logger.error("❌ Credenziali SMTP mancanti")
            return False, track_id
        try:
            if SMTP_PORT == 465:
                server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30)
            else:
                server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
                server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(from_addr, [to], msg.as_string())
            server.quit()
            return True, track_id
        except Exception as e:
            logger.error(f"❌ Errore SMTP per {to}: {e}")
            return False, track_id
    return False, track_id
