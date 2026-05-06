import os, uuid, re, urllib.parse, logging, json, smtplib, mimetypes
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv

load_dotenv()

PROVIDER = os.getenv("EMAIL_PROVIDER", "dry_run")
APP_BASE = os.getenv("APP_BASE_URL", "http://localhost:8000")
TEST_DIR = Path(os.getenv("TEST_EMAILS_DIR", "test_emails"))
LINKS_DB = Path("links_registry.json")

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

logger = logging.getLogger(__name__)

def _load_links(): return json.loads(LINKS_DB.read_text()) if LINKS_DB.exists() else {}
def _save_links(db): LINKS_DB.write_text(json.dumps(db))

def inject_tracking(html: str, track_id: str) -> str:
    pixel = f'<img src="{APP_BASE}/track/open/{track_id}" width="1" height="1" style="display:none;">'
    html = html.replace("</body>", f"{pixel}</body>")
    def repl(m):
        url = m.group(1)
        if not url.startswith(("http://", "https://", "mailto:")): return m.group(0)
        lid = uuid.uuid4().hex[:8]
        db = _load_links(); db[lid] = url; _save_links(db)
        return f'<a href="{APP_BASE}/track/click/{lid}?url={urllib.parse.quote(url, safe="")}"'
    return re.sub(r'<a\s+[^>]*href=["\'](.*?)["\']', repl, html)

def send_email(to: str, subject: str, html: str, track: bool = True, attachment_path: str = None) -> bool:
    # ✅ FIX: Aggiungi automaticamente footer unsubscribe se non presente
    if "unsubscribe" not in html.lower():
        unsub_link = f"{APP_BASE}/unsubscribe?email={urllib.parse.quote(to, safe='')}"
        footer = f'<hr style="border:0;border-top:1px solid #e2e8f0;margin:20px 0;"><p style="font-size:12px;color:#64748b;text-align:center;">Se non desideri più ricevere queste email, <a href="{unsub_link}" style="color:#3b82f6;">clicca qui per annullare l\'iscrizione</a>.</p>'
        if "</body>" in html:
            html = html.replace("</body>", f"{footer}</body>")
        else:
            html += footer

    if track:
        html = inject_tracking(html, str(uuid.uuid4()))

    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_USER if PROVIDER == "smtp" else "test@test.it"
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html"))

    if attachment_path and os.path.exists(attachment_path):
        try:
            ctype, encoding = mimetypes.guess_type(attachment_path)
            if ctype is None or encoding is not None:
                ctype = 'application/octet-stream'
            maintype, subtype = ctype.split('/', 1)
            with open(attachment_path, "rb") as fp:
                file_part = MIMEBase(maintype, subtype)
                file_part.set_payload(fp.read())
            encoders.encode_base64(file_part)
            filename = os.path.basename(attachment_path)
            file_part.add_header('Content-Disposition', 'attachment', filename=filename)
            msg.attach(file_part)
        except Exception as e:
            logger.error(f"❌ Errore allegato: {e}")

    if PROVIDER == "dry_run":
        TEST_DIR.mkdir(exist_ok=True)
        safe = "".join(c for c in to if c.isalnum() or c in "@._-")
        path = TEST_DIR / f"{safe}_{subject[:15]}.html"
        path.write_text(html, encoding="utf-8")
        return True

    if PROVIDER == "smtp":
        if not SMTP_USER or not SMTP_PASSWORD:
            logger.error("❌ Credenziali SMTP mancanti")
            return False
        try:
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) if SMTP_PORT == 465 else smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            if SMTP_PORT != 465: server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to, msg.as_string())
            server.quit()
            return True
        except Exception as e:
            logger.error(f"❌ Errore SMTP: {e}")
            return False
    return False