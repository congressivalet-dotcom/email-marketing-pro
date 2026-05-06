import logging
import os
from datetime import datetime
from jinja2 import Environment
from apscheduler.schedulers.background import BackgroundScheduler
from database import SessionLocal
from models import Campaign, Template, CampaignLog
from email_service import send_email

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler()
jinja_env = Environment(autoescape=True)

def check_campaigns():
    # ✅ FIX: Crea una nuova sessione DB per questa operazione
    db = SessionLocal()
    try:
        now = datetime.now()
        logger.info(f"⏱️ Scheduler check ore: {now}")
        
        # Cerca campagne 'scheduled' con data passata o attuale
        camps = db.query(Campaign).filter(
            Campaign.status == "scheduled",
            Campaign.scheduled_at <= now
        ).all()
        
        logger.info(f"🔍 Trovate {len(camps)} campagne da elaborare")

        if not camps:
            return

        for c in camps:
            try:
                logger.info(f"🚀 ELABORO CAMPAGNA: '{c.name}' (ID: {c.id})")
                c.status = "running"
                db.commit()  # Aggiorna stato a "running"

                tpl = db.query(Template).filter(Template.id == c.template_id).first()
                if not tpl:
                    logger.error(f"❌ Template ID {c.template_id} non trovato per campagna {c.id}")
                    c.status = "failed"
                    db.commit()
                    continue

                recipients = c.list.recipients if c.list else []
                logger.info(f"👥 Destinatari nella lista '{c.list.name}': {len(recipients)}")
                
                sent_count = 0
                logs_to_add = []  # ✅ FIX: Accumula i log invece di fare commit multipli
                
                for r in recipients:
                    if r.status == "pending":
                        try:
                            ctx = {
                                "nome": r.nome, "cognome": r.cognome, "email": r.email,
                                "variabile1": r.var1, "variabile2": r.var2, "variabile3": r.var3,
                                "variabile4": r.var4, "variabile5": r.var5, "campaign": c.name
                            }
                            html = jinja_env.from_string(tpl.html_content).render(**ctx)
                            
                            attachment_to_send = None
                            if r.attachment_filename:
                                pers_path = os.path.join("uploads", "personalized", r.attachment_filename)
                                if os.path.exists(pers_path):
                                    attachment_to_send = pers_path
                            
                            if not attachment_to_send and tpl.attachment_path and os.path.exists(tpl.attachment_path):
                                attachment_to_send = tpl.attachment_path
                            
                            result = send_email(r.email, tpl.subject, html, track=True, attachment_path=attachment_to_send)
                            
                            if result:
                                r.status = "sent"
                                sent_count += 1
                                
                                # ✅ FIX: Crea il log ma non fare commit subito
                                log_entry = CampaignLog(
                                    campaign_id=c.id,
                                    recipient_id=r.id,
                                    status="sent"
                                )
                                logs_to_add.append(log_entry)
                                
                                logger.info(f"   ✅ Inviato a {r.email}")
                            else:
                                logger.warning(f"   ⚠️ Fallito invio a {r.email}")
                                
                        except Exception as e:
                            logger.error(f"   ❌ Errore elaborazione {r.email}: {e}", exc_info=True)
                
                # ✅ FIX: Aggiungi tutti i log in una volta sola
                if logs_to_add:
                    db.add_all(logs_to_add)
                
                # Aggiorna stato campagna
                c.status = "completed"
                db.commit()  # ✅ Un solo commit finale per tutto
                
                logger.info(f"✅ CAMPAGNA COMPLETATA: '{c.name}' | Email inviate: {sent_count}/{len(recipients)}")
                
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
    scheduler.add_job(check_campaigns, "interval", minutes=1, id="campaign_checker", replace_existing=True)
    scheduler.start()
    logger.info("⏱️ Scheduler AVVIATO (controllo ogni 60 secondi)")

def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("⏹️ Scheduler FERMATO")

def send_campaign_now(campaign_id: int):
    """Forza l'invio immediato di una campagna per debug"""
    db = SessionLocal()
    try:
        c = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not c:
            return {"error": "Campagna non trovata"}
        
        logger.info(f"🔧 DEBUG: Invio forzato campagna '{c.name}'")
        c.status = "running"
        db.commit()
        
        tpl = db.query(Template).filter(Template.id == c.template_id).first()
        if not tpl:
            return {"error": "Template non trovato"}
        
        sent = 0
        logs_to_add = []  # ✅ FIX: Accumula i log
        
        for r in c.list.recipients:
            if r.status == "pending":
                ctx = {k: getattr(r, k, "") for k in ["nome", "cognome", "email", "var1", "var2", "var3", "var4", "var5"]}
                ctx["campaign"] = c.name
                html = jinja_env.from_string(tpl.html_content).render(**ctx)
                
                attachment = None
                if r.attachment_filename:
                    p = os.path.join("uploads", "personalized", r.attachment_filename)
                    if os.path.exists(p): attachment = p
                if not attachment and tpl.attachment_path and os.path.exists(tpl.attachment_path):
                    attachment = tpl.attachment_path
                
                if send_email(r.email, tpl.subject, html, track=True, attachment_path=attachment):
                    r.status = "sent"
                    sent += 1
                    
                    # ✅ FIX: Accumula invece di commit multipli
                    log_entry = CampaignLog(campaign_id=c.id, recipient_id=r.id, status="sent")
                    logs_to_add.append(log_entry)
        
        # ✅ FIX: Aggiungi tutti i log in una volta
        if logs_to_add:
            db.add_all(logs_to_add)
        
        c.status = "completed"
        db.commit()  # ✅ Un solo commit finale
        return {"success": True, "sent": sent, "campaign": c.name}
        
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Debug send failed: {e}", exc_info=True)
        return {"error": str(e)}
    finally:
        db.close()