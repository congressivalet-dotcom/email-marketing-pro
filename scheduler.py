"""
Scheduler per l'invio automatico delle campagne.

Controlla ogni minuto se ci sono campagne con stato 'scheduled' la cui data
di invio è arrivata, e le elabora.

Per ogni invio:
- genera un track_id univoco
- lo passa a send_email() così il pixel di apertura sarà legato a questo invio
- crea un CampaignLog con lo stesso track_id, per poter correlare le aperture
"""
import os
import uuid
import logging
from datetime import datetime
from jinja2 import Environment, select_autoescape
from apscheduler.schedulers.background import BackgroundScheduler

from database import SessionLocal
from models import Campaign, Template, CampaignLog
from email_service import send_email

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler()
jinja_env = Environment(autoescape=select_autoescape(["html", "xml"]))


def _build_context(recipient, campaign_name: str) -> dict:
    return {
        "nome": recipient.nome or "",
        "cognome": recipient.cognome or "",
        "email": recipient.email,
        "variabile1": recipient.var1 or "",
        "variabile2": recipient.var2 or "",
        "variabile3": recipient.var3 or "",
        "variabile4": recipient.var4 or "",
        "variabile5": recipient.var5 or "",
        "campaign": campaign_name,
    }


def _resolve_attachment(recipient, template) -> str:
    """Restituisce il path dell'allegato da usare (personale o del template)."""
    if recipient.attachment_filename:
        pers_path = os.path.join("uploads", "personalized", recipient.attachment_filename)
        if os.path.exists(pers_path):
            return pers_path
    if template.attachment_path and os.path.exists(template.attachment_path):
        return template.attachment_path
    return None


def _process_campaign(db, c: Campaign) -> int:
    """Elabora una singola campagna, ritorna il numero di email inviate."""
    logger.info(f"🚀 Elaboro campagna: '{c.name}' (ID: {c.id})")
    c.status = "running"
    db.commit()

    tpl = db.query(Template).filter(Template.id == c.template_id).first()
    if not tpl:
        logger.error(f"❌ Template ID {c.template_id} non trovato per campagna {c.id}")
        c.status = "failed"
        db.commit()
        return 0

    recipients = c.list.recipients if c.list else []
    logger.info(f"👥 Destinatari nella lista '{c.list.name}': {len(recipients)}")

    sent_count = 0
    logs_to_add = []

    for r in recipients:
        # Skip unsubscribed
        if r.status == "unsubscribed":
            logger.info(f"   ⏭️  Saltato {r.email} (unsubscribed)")
            continue

        try:
            ctx = _build_context(r, c.name)
            html = jinja_env.from_string(tpl.html_content).render(**ctx)
            attachment = _resolve_attachment(r, tpl)

            track_id = uuid.uuid4().hex
            ok, _ = send_email(
                r.email,
                tpl.subject,
                html,
                track=True,
                attachment_path=attachment,
                campaign_id=c.id,
                track_id=track_id,
            )

            if ok:
                sent_count += 1
                logs_to_add.append(
                    CampaignLog(
                        campaign_id=c.id,
                        recipient_id=r.id,
                        track_id=track_id,
                        status="sent",
                    )
                )
                logger.info(f"   ✅ Inviato a {r.email}")
            else:
                logs_to_add.append(
                    CampaignLog(
                        campaign_id=c.id,
                        recipient_id=r.id,
                        track_id=track_id,
                        status="failed",
                    )
                )
                logger.warning(f"   ⚠️  Fallito invio a {r.email}")
        except Exception as e:
            logger.error(f"   ❌ Errore elaborazione {r.email}: {e}", exc_info=True)

    if logs_to_add:
        db.add_all(logs_to_add)
    c.status = "completed"
    db.commit()
    logger.info(
        f"✅ Campagna completata: '{c.name}' | "
        f"{sent_count}/{len(recipients)} email inviate"
    )
    return sent_count


def check_campaigns():
    """Job dello scheduler: cerca campagne pending con data passata."""
    db = SessionLocal()
    try:
        now = datetime.now()
        camps = (
            db.query(Campaign)
            .filter(Campaign.status == "scheduled", Campaign.scheduled_at <= now)
            .all()
        )
        if camps:
            logger.info(f"🔍 Trovate {len(camps)} campagne da elaborare")
        for c in camps:
            try:
                _process_campaign(db, c)
            except Exception as e:
                logger.error(f"❌ Errore campagna '{c.name}': {e}", exc_info=True)
                c.status = "failed"
                db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Errore scheduler principale: {e}", exc_info=True)
    finally:
        db.close()


def start_scheduler():
    if not scheduler.running:
        scheduler.add_job(
            check_campaigns,
            "interval",
            minutes=1,
            id="campaign_checker",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("⏱️  Scheduler avviato (controllo ogni 60 secondi)")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("⏹️  Scheduler fermato")


def send_campaign_now(campaign_id: int) -> dict:
    """Forza l'invio immediato di una campagna (per debug o invio manuale)."""
    db = SessionLocal()
    try:
        c = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not c:
            return {"error": "Campagna non trovata"}
        logger.info(f"🔧 Invio forzato campagna '{c.name}'")
        sent = _process_campaign(db, c)
        return {"success": True, "sent": sent, "campaign": c.name}
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Send-now failed: {e}", exc_info=True)
        return {"error": str(e)}
    finally:
        db.close()
