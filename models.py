from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Table
from sqlalchemy.orm import relationship, declarative_base
from database import Base
from datetime import datetime

# Tabella di associazione Molti-a-Molti per Liste <-> Contatti
recipient_list_association = Table(
    "recipient_list_association",
    Base.metadata,
    Column("list_id", Integer, ForeignKey("recipient_lists.id"), primary_key=True),
    Column("recipient_id", Integer, ForeignKey("recipients.id"), primary_key=True)
)

class Recipient(Base):
    __tablename__ = "recipients"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    nome = Column(String, default="")
    cognome = Column(String, default="")
    var1 = Column(String, default="")
    var2 = Column(String, default="")
    var3 = Column(String, default="")
    var4 = Column(String, default="")
    var5 = Column(String, default="")
    status = Column(String, default="pending")
    attachment_filename = Column(String, default="")
    lists = relationship("RecipientList", secondary=recipient_list_association, back_populates="recipients")

class RecipientList(Base):
    __tablename__ = "recipient_lists"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    recipients = relationship("Recipient", secondary=recipient_list_association, back_populates="lists")

class Template(Base):
    __tablename__ = "templates"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    subject = Column(String, nullable=False)
    html_content = Column(Text, nullable=False)
    attachment_path = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

class Campaign(Base):
    __tablename__ = "campaigns"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    list_id = Column(Integer, ForeignKey("recipient_lists.id"), nullable=False)
    template_id = Column(Integer, ForeignKey("templates.id"), nullable=False)
    scheduled_at = Column(DateTime, nullable=False)
    status = Column(String, default="draft")
    list = relationship("RecipientList")
    template = relationship("Template")

class EmailEvent(Base):
    __tablename__ = "email_events"
    id = Column(Integer, primary_key=True, index=True)
    track_id = Column(String, index=True)
    email = Column(String, index=True)
    event_type = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)
    ip = Column(String)
    user_agent = Column(String)

# ✅ NUOVO: Tabella per la cronologia degli invii
class CampaignLog(Base):
    __tablename__ = "campaign_logs"
    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))
    recipient_id = Column(Integer, ForeignKey("recipients.id"))
    status = Column(String, default="sent")
    sent_at = Column(DateTime, default=datetime.utcnow)