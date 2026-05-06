"""
Email Marketing Pro - FastAPI

Applicazione completa per gestione di liste, template, campagne email
con tracking aperture/click integrato.
"""
import os
import csv
import io
import logging
import uuid
from datetime import datetime
from urllib.parse import quote

from fastapi import (
    FastAPI, Request, Form, Depends, HTTPException, Query, UploadFile, File,
)
from fastapi.responses import (
    Response, HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse,
)
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from database import init_db, get_db
from models import (
    Recipient, RecipientList, EmailEvent, Campaign, Template, CampaignLog,
    TrackingLink, recipient_list_association,
)
from email_service import send_email
from scheduler import start_scheduler, stop_scheduler, send_campaign_now

# ─── CONFIG ────────────────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(message)s",
)

app = FastAPI(title="Email Marketing Pro")

SECRET = os.getenv("SESSION_SECRET", "change-me-in-production-32chars-min")
app.add_middleware(SessionMiddleware, secret_key=SECRET, https_only=False, same_site="lax")

ADMIN_USER = os.getenv("DASHBOARD_USER", "admin")
ADMIN_PASS = os.getenv("DASHBOARD_PASSWORD", "admin123")

# Pixel GIF 1x1 trasparente
PIXEL = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04"
    b"\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)

# Template predefiniti per la galleria
PREMADE_TEMPLATES = [
    {
        "id": "minimal",
        "name": "Minimal",
        "preview": "Design pulito e moderno",
        "html": """<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;max-width:600px;margin:auto;padding:32px;background:#ffffff;color:#1e293b;line-height:1.6;">
<h2 style="color:#1e293b;font-size:24px;margin:0 0 16px;">Ciao {{ nome }},</h2>
<p style="color:#475569;margin:0 0 16px;">{{ variabile1 }}</p>
<p style="color:#475569;margin:0 0 24px;">{{ variabile2 }}</p>
<a href="https://example.com" style="display:inline-block;background:#4f46e5;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;">Scopri di più</a>
</div>""",
    },
    {
        "id": "corporate",
        "name": "Corporate",
        "preview": "Business professionale",
        "html": """<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;max-width:600px;margin:auto;background:#ffffff;border:1px solid #e2e8f0;">
<div style="background:linear-gradient(135deg,#4f46e5 0%,#7c3aed 100%);color:#fff;padding:32px 24px;text-align:center;">
<h1 style="margin:0;font-size:28px;">{{ variabile1 }}</h1>
</div>
<div style="padding:32px 24px;">
<p style="color:#1e293b;font-size:16px;margin:0 0 16px;">Gentile {{ nome }} {{ cognome }},</p>
<p style="color:#475569;line-height:1.6;margin:0 0 24px;">{{ variabile2 }}</p>
<a href="https://example.com" style="display:inline-block;background:#1e293b;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;">Maggiori informazioni</a>
</div>
</div>""",
    },
    {
        "id": "promo",
        "name": "Promo",
        "preview": "Offerte e sconti",
        "html": """<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;max-width:600px;margin:auto;background:#ffffff;border:2px solid #f59e0b;">
<div style="background:linear-gradient(135deg,#f59e0b 0%,#ef4444 100%);color:#fff;padding:40px 24px;text-align:center;">
<div style="font-size:14px;letter-spacing:3px;opacity:.9;margin-bottom:8px;">OFFERTA SPECIALE</div>
<h1 style="margin:0;font-size:32px;">{{ variabile1 }}</h1>
</div>
<div style="padding:32px 24px;">
<p style="color:#1e293b;font-size:18px;margin:0 0 16px;">Ciao {{ nome }},</p>
<p style="color:#475569;line-height:1.6;margin:0 0 24px;">{{ variabile2 }}</p>
<div style="text-align:center;">
<a href="https://example.com" style="display:inline-block;background:#ef4444;color:#fff;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:16px;">Approfitta ora</a>
</div>
</div>
</div>""",
    },
    {
        "id": "newsletter",
        "name": "Newsletter",
        "preview": "Aggiornamenti periodici",
        "html": """<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;max-width:600px;margin:auto;background:#ffffff;border-top:4px solid #4f46e5;">
<div style="padding:24px;text-align:center;border-bottom:1px solid #e2e8f0;">
<h1 style="color:#4f46e5;margin:0;font-size:24px;">{{ variabile1 }}</h1>
<p style="color:#94a3b8;font-size:14px;margin:8px 0 0;">Newsletter — {{ campaign }}</p>
</div>
<div style="padding:32px 24px;">
<p style="color:#1e293b;font-size:16px;margin:0 0 16px;">Ciao {{ nome }},</p>
<p style="color:#475569;line-height:1.6;margin:0 0 24px;">{{ variabile2 }}</p>
<p style="color:#475569;line-height:1.6;margin:0 0 24px;">{{ variabile3 }}</p>
</div>
</div>""",
    },
]


# ─── UI: BASE LAYOUT ────────────────────────────────────────────────────────
def base_head(title: str = "Email Marketing Pro") -> str:
    """Restituisce <head> + apertura body con stili comuni."""
    return f"""<!DOCTYPE html><html lang="it"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<script src="https://cdn.tailwindcss.com"></script>
<script>
tailwind.config = {{
  theme: {{ extend: {{
    colors: {{
      brand: {{ 50:'#eef2ff',100:'#e0e7ff',500:'#6366f1',600:'#4f46e5',700:'#4338ca',900:'#312e81' }}
    }},
    fontFamily: {{ sans: ['Inter', '-apple-system', 'system-ui', 'sans-serif'] }}
  }} }}
}};
</script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }}
.btn-primary {{ background:linear-gradient(135deg,#4f46e5 0%,#6366f1 100%); }}
.btn-primary:hover {{ background:linear-gradient(135deg,#4338ca 0%,#4f46e5 100%); }}
.card {{ background:#fff; border-radius:12px; box-shadow:0 1px 3px rgba(0,0,0,0.05),0 1px 2px rgba(0,0,0,0.03); border:1px solid #f1f5f9; }}
input:focus, select:focus, textarea:focus {{ outline:none; border-color:#6366f1; box-shadow:0 0 0 3px rgba(99,102,241,0.15); }}
</style>
</head><body class="bg-slate-50 min-h-screen">
"""


def header(current: str = "") -> str:
    """Header di navigazione con logo + menu."""
    items = [
        ("dashboard", "/", "Dashboard",
         '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/></svg>'),
        ("lists", "/lists", "Liste",
         '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14-7H5m14 14H5"/></svg>'),
        ("contacts", "/contacts", "Contatti",
         '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"/></svg>'),
        ("templates", "/templates", "Template",
         '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>'),
        ("templates-list", "/templates-list", "Libreria",
         '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14-7H5m14 14H5"/></svg>'),
        ("schedule", "/schedule", "Campagne",
         '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>'),
        ("campaigns", "/campaigns", "Cronologia",
         '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>'),
    ]
    nav = ""
    for key, url, label, icon in items:
        active = key == current
        cls = "bg-white/10 text-white" if active else "text-slate-300 hover:bg-white/5 hover:text-white"
        nav += (
            f'<a href="{url}" class="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium '
            f'transition {cls}">{icon}<span class="hidden lg:inline">{label}</span></a>'
        )
    return f"""<nav class="bg-slate-900 text-white shadow-lg sticky top-0 z-50 border-b border-slate-800">
<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
<div class="flex justify-between items-center h-16">
<a href="/" class="flex items-center gap-3">
<div class="w-9 h-9 rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 flex items-center justify-center shadow-md">
<svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
</div>
<div><div class="font-bold text-base leading-tight">Email Marketing Pro</div></div>
</a>
<div class="flex items-center gap-1">{nav}
<form method="post" action="/logout" class="ml-2">
<button class="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium bg-red-600/90 hover:bg-red-600 text-white transition">
<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/></svg>
<span class="hidden lg:inline">Esci</span>
</button>
</form>
</div></div></div></nav>
"""


# ─── STARTUP / SHUTDOWN ────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("uploads/personalized", exist_ok=True)
    os.makedirs("test_emails", exist_ok=True)
    init_db()
    start_scheduler()
    logging.info("✅ Sistema pronto")


@app.on_event("shutdown")
def shutdown():
    stop_scheduler()


async def require_auth(req: Request):
    if not req.session.get("auth"):
        return RedirectResponse(url="/login", status_code=303)


# ─── LOGIN ──────────────────────────────────────────────────────────────────
LOGIN_HTML = """<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Accesso · Email Marketing Pro</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>body { font-family:'Inter',sans-serif; }</style>
</head>
<body class="min-h-screen flex items-center justify-center p-4" style="background:linear-gradient(135deg,#1e1b4b 0%,#312e81 50%,#1e293b 100%);">
<div class="w-full max-w-md">
<div class="bg-white rounded-2xl shadow-2xl p-8">
<div class="text-center mb-8">
<div class="w-16 h-16 rounded-2xl mx-auto mb-4 flex items-center justify-center" style="background:linear-gradient(135deg,#4f46e5 0%,#7c3aed 100%);">
<svg class="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
</div>
<h1 class="text-2xl font-bold text-slate-800">Email Marketing Pro</h1>
<p class="text-slate-500 mt-2 text-sm">Accedi al pannello di amministrazione</p>
</div>
<form method="post" action="/login" class="space-y-5">
<div>
<label class="block text-sm font-semibold text-slate-700 mb-2">Username</label>
<input name="username" placeholder="admin" required
class="w-full px-4 py-3 border border-slate-300 rounded-lg focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100">
</div>
<div>
<label class="block text-sm font-semibold text-slate-700 mb-2">Password</label>
<input name="password" type="password" placeholder="••••••••" required
class="w-full px-4 py-3 border border-slate-300 rounded-lg focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100">
</div>
__ERROR__
<button type="submit" class="w-full text-white py-3 rounded-lg font-semibold transition shadow-md hover:shadow-lg"
style="background:linear-gradient(135deg,#4f46e5 0%,#7c3aed 100%);">
Accedi
</button>
</form>
</div>
<p class="text-center text-white/60 text-xs mt-6">© 2026 Email Marketing Pro — Tutti i diritti riservati</p>
</div></body></html>"""


@app.get("/login")
def login_form():
    return HTMLResponse(LOGIN_HTML.replace("__ERROR__", ""))


@app.post("/login")
def login_proc(req: Request, username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USER and password == ADMIN_PASS:
        req.session["auth"] = True
        req.session["user"] = username
        return RedirectResponse(url="/", status_code=303)
    err = '<div class="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg p-3 text-center">❌ Credenziali errate</div>'
    return HTMLResponse(LOGIN_HTML.replace("__ERROR__", err), status_code=401)


@app.post("/logout")
def logout(req: Request):
    req.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ─── DASHBOARD ──────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def dashboard(req: Request = Depends(require_auth)):
    return HTMLResponse(base_head("Dashboard") + header("dashboard") + """
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">

<!-- Page header -->
<div>
<h1 class="text-2xl font-bold text-slate-900">Dashboard</h1>
<p class="text-slate-500 text-sm mt-1">Panoramica delle tue campagne email</p>
</div>

<!-- Stat cards -->
<div class="grid grid-cols-2 lg:grid-cols-4 gap-4" id="stat-cards">
<div class="card p-5"><div class="flex items-center justify-between mb-2">
<span class="text-xs font-semibold text-slate-500 uppercase tracking-wide">Contatti totali</span>
<div class="w-9 h-9 rounded-lg bg-indigo-50 flex items-center justify-center"><svg class="w-5 h-5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg></div>
</div><div class="text-3xl font-bold text-slate-900" id="stat-contacts">—</div></div>

<div class="card p-5"><div class="flex items-center justify-between mb-2">
<span class="text-xs font-semibold text-slate-500 uppercase tracking-wide">Email inviate</span>
<div class="w-9 h-9 rounded-lg bg-emerald-50 flex items-center justify-center"><svg class="w-5 h-5 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg></div>
</div><div class="text-3xl font-bold text-slate-900" id="stat-sent">—</div></div>

<div class="card p-5"><div class="flex items-center justify-between mb-2">
<span class="text-xs font-semibold text-slate-500 uppercase tracking-wide">Aperture totali</span>
<div class="w-9 h-9 rounded-lg bg-sky-50 flex items-center justify-center"><svg class="w-5 h-5 text-sky-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg></div>
</div><div class="text-3xl font-bold text-slate-900" id="stat-opens">—</div></div>

<div class="card p-5"><div class="flex items-center justify-between mb-2">
<span class="text-xs font-semibold text-slate-500 uppercase tracking-wide">Tasso apertura</span>
<div class="w-9 h-9 rounded-lg bg-amber-50 flex items-center justify-center"><svg class="w-5 h-5 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/></svg></div>
</div><div class="text-3xl font-bold text-slate-900" id="stat-rate">—</div></div>
</div>

<!-- Quick actions -->
<div class="grid grid-cols-1 md:grid-cols-3 gap-4">
<button id="btn-test" class="card p-5 hover:shadow-md transition text-left group">
<div class="w-10 h-10 rounded-lg bg-indigo-50 group-hover:bg-indigo-100 flex items-center justify-center mb-3 transition"><svg class="w-5 h-5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"/></svg></div>
<div class="font-semibold text-slate-900">Invia Test</div>
<div class="text-slate-500 text-sm mt-0.5">Testa l'invio di una email</div>
</button>

<a href="/api/export" class="card p-5 hover:shadow-md transition text-left group block">
<div class="w-10 h-10 rounded-lg bg-emerald-50 group-hover:bg-emerald-100 flex items-center justify-center mb-3 transition"><svg class="w-5 h-5 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg></div>
<div class="font-semibold text-slate-900">Esporta Eventi</div>
<div class="text-slate-500 text-sm mt-0.5">Scarica CSV con aperture e click</div>
</a>

<a href="/api/sample-csv" class="card p-5 hover:shadow-md transition text-left group block">
<div class="w-10 h-10 rounded-lg bg-amber-50 group-hover:bg-amber-100 flex items-center justify-center mb-3 transition"><svg class="w-5 h-5 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg></div>
<div class="font-semibold text-slate-900">CSV di Esempio</div>
<div class="text-slate-500 text-sm mt-0.5">Scarica il template per importare contatti</div>
</a>
</div>

<!-- Chart -->
<div class="card p-6">
<div class="flex items-center justify-between mb-4">
<div>
<h2 class="text-lg font-semibold text-slate-900">Statistiche</h2>
<p class="text-sm text-slate-500">Aperture e click degli ultimi 30 giorni</p>
</div>
<button id="refreshChart" class="text-sm text-indigo-600 hover:text-indigo-700 font-medium flex items-center gap-1">
<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
Aggiorna
</button>
</div>
<div class="h-72"><canvas id="chart"></canvas></div>
<div id="chart-empty" class="hidden text-center py-12 text-slate-400">
<svg class="w-12 h-12 mx-auto mb-3 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
<p class="text-sm">Nessun evento registrato</p>
<p class="text-xs mt-1">Le statistiche appariranno dopo l'invio della prima campagna</p>
</div>
</div>

<!-- Log -->
<div class="card p-5">
<h2 class="text-sm font-semibold text-slate-700 mb-3 flex items-center gap-2">
<svg class="w-4 h-4 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
Log Operazioni
</h2>
<div id="log" class="bg-slate-900 rounded-lg p-3 font-mono text-xs text-emerald-400 h-28 overflow-auto">$ Pronto</div>
</div>
</div>

<script>
let chart = null;

async function loadStats() {
  try {
    const r = await fetch('/api/stats');
    const d = await r.json();

    // Stat cards
    document.getElementById('stat-contacts').textContent = d.total_contacts.toLocaleString('it-IT');
    document.getElementById('stat-sent').textContent = d.total_sent.toLocaleString('it-IT');
    document.getElementById('stat-opens').textContent = d.total_opens.toLocaleString('it-IT');
    const rate = d.total_sent > 0 ? ((d.total_opens / d.total_sent) * 100).toFixed(1) : '0.0';
    document.getElementById('stat-rate').textContent = rate + '%';

    // Chart
    const ctx = document.getElementById('chart').getContext('2d');
    const empty = document.getElementById('chart-empty');
    const canvas = document.getElementById('chart');

    if (d.dates.length === 0) {
      canvas.style.display = 'none';
      empty.classList.remove('hidden');
      return;
    }
    canvas.style.display = 'block';
    empty.classList.add('hidden');

    if (chart) chart.destroy();

    chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: d.dates,
        datasets: [
          { label: 'Aperture', data: d.opens, borderColor: '#4f46e5', backgroundColor: 'rgba(79,70,229,0.08)', fill: true, tension: 0.35, borderWidth: 2, pointRadius: 3, pointBackgroundColor: '#4f46e5' },
          { label: 'Click', data: d.clicks, borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,0.08)', fill: true, tension: 0.35, borderWidth: 2, pointRadius: 3, pointBackgroundColor: '#10b981' }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', labels: { usePointStyle: true, padding: 15, font: { family: 'Inter', size: 12 } } } },
        scales: {
          y: { beginAtZero: true, ticks: { stepSize: 1, font: { family: 'Inter' } }, grid: { color: '#f1f5f9' } },
          x: { ticks: { font: { family: 'Inter' } }, grid: { display: false } }
        }
      }
    });
  } catch (e) {
    console.error('Errore stats:', e);
  }
}

document.getElementById('refreshChart').addEventListener('click', loadStats);
document.getElementById('btn-test').addEventListener('click', async () => {
  const log = document.getElementById('log');
  log.innerHTML += '\\n$ Invio email di test...';
  try {
    const r = await fetch('/api/test', { method: 'POST' });
    const t = await r.text();
    log.innerHTML += '\\n' + t;
    log.scrollTop = log.scrollHeight;
  } catch (e) { log.innerHTML += '\\n❌ Errore: ' + e; }
});

loadStats();
setInterval(loadStats, 30000);
</script>
</body></html>""")


@app.post("/api/test")
def api_test(req: Request = Depends(require_auth)):
    try:
        ok, _ = send_email(
            "sclerotherapycongress@gmail.com", "Test Email Marketing Pro",
            "<h1>Email di test</h1><p>Questa è un'email di prova generata dal sistema.</p>"
            '<p><a href="https://valet.it">Clicca qui per testare il tracking</a></p>',
        )
        return HTMLResponse("✅ Email di test inviata correttamente" if ok else "❌ Errore invio test", status_code=200 if ok else 500)
    except Exception as e:
        return HTMLResponse(f"❌ Errore: {e}", status_code=500)


@app.get("/api/stats")
def api_stats(req: Request = Depends(require_auth), db: Session = Depends(get_db)):
    # Ultimi 30 giorni
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=30)
    evts = db.query(EmailEvent).filter(EmailEvent.timestamp >= cutoff).all()

    opens, clicks = {}, {}
    for e in evts:
        d = e.timestamp.strftime("%Y-%m-%d")
        if e.event_type == "open":
            opens[d] = opens.get(d, 0) + 1
        elif e.event_type == "click":
            clicks[d] = clicks.get(d, 0) + 1

    dates = sorted(set(list(opens.keys()) + list(clicks.keys())))
    total_opens = sum(opens.values())
    total_clicks = sum(clicks.values())
    total_sent = db.query(CampaignLog).filter(CampaignLog.status == "sent").count()
    total_contacts = db.query(Recipient).count()

    return {
        "dates": dates,
        "opens": [opens.get(d, 0) for d in dates],
        "clicks": [clicks.get(d, 0) for d in dates],
        "total_opens": total_opens,
        "total_clicks": total_clicks,
        "total_sent": total_sent,
        "total_contacts": total_contacts,
    }


@app.get("/api/export")
def api_export(req: Request = Depends(require_auth), db: Session = Depends(get_db)):
    evts = db.query(EmailEvent).order_by(EmailEvent.timestamp.desc()).all()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "track_id", "email", "campaign_id", "type", "time", "ip", "user_agent"])
    for e in evts:
        w.writerow([
            e.id, e.track_id, e.email or "", e.campaign_id or "", e.event_type,
            e.timestamp.isoformat(), e.ip or "", e.user_agent or "",
        ])
    return StreamingResponse(
        iter([out.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=email_events.csv"},
    )


@app.get("/api/sample-csv")
def api_sample_csv(req: Request = Depends(require_auth)):
    """Scarica un CSV di esempio per importare contatti."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["email", "nome", "cognome", "var1", "var2", "var3", "var4", "var5"])
    w.writerow(["mario.rossi@esempio.it", "Mario", "Rossi", "Cliente VIP", "Roma", "+39 333 1234567", "Acquisto Maggio", ""])
    w.writerow(["laura.bianchi@esempio.it", "Laura", "Bianchi", "Standard", "Milano", "", "Newsletter", "Lead 2026"])
    w.writerow(["giulia.verdi@esempio.it", "Giulia", "Verdi", "", "Napoli", "+39 320 9876543", "", ""])
    return StreamingResponse(
        iter([out.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=esempio_contatti.csv"},
    )


# ─── UNSUBSCRIBE ────────────────────────────────────────────────────────────
UNSUBSCRIBE_PAGE = """<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Disiscrizione</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>body{font-family:'Inter',sans-serif}</style></head>
<body class="min-h-screen flex items-center justify-center bg-slate-50 p-4">
<div class="max-w-md w-full bg-white rounded-2xl shadow-lg p-8 text-center">
__CONTENT__
</div></body></html>"""


@app.get("/unsubscribe")
def unsubscribe(email: str = Query(...), db: Session = Depends(get_db)):
    rec = db.query(Recipient).filter(Recipient.email == email).first()
    if rec:
        rec.status = "unsubscribed"
        db.commit()
        content = """
<div class="text-5xl mb-4">👋</div>
<h2 class="text-2xl font-bold text-slate-800 mb-3">Ci dispiace vederti andare</h2>
<p class="text-slate-600 mb-4 leading-relaxed">
La tua disiscrizione è stata registrata. Non riceverai più nostre comunicazioni.
</p>
<p class="text-slate-500 text-sm italic">Grazie del tempo che ci hai dedicato.</p>
"""
    else:
        content = """
<div class="text-5xl mb-4">⚠️</div>
<h2 class="text-2xl font-bold text-slate-800 mb-3">Email non trovata</h2>
<p class="text-slate-600">L'indirizzo email indicato non risulta nelle nostre liste.</p>
"""
    return HTMLResponse(UNSUBSCRIBE_PAGE.replace("__CONTENT__", content))


# ─── TRACKING ───────────────────────────────────────────────────────────────
@app.get("/track/open/{tid}")
def track_open(tid: str, req: Request, email: str = Query(""), campaign_id: int = Query(None), db: Session = Depends(get_db)):
    db.add(EmailEvent(
        track_id=tid, email=email, campaign_id=campaign_id, event_type="open",
        ip=req.client.host if req.client else "",
        user_agent=req.headers.get("user-agent", "")[:500],
    ))
    db.commit()
    return Response(content=PIXEL, media_type="image/gif", headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})


@app.get("/track/click/{lid}")
def track_click(lid: str, req: Request, email: str = Query(""), campaign_id: int = Query(None), db: Session = Depends(get_db)):
    link = db.query(TrackingLink).filter(TrackingLink.link_id == lid).first()
    if not link:
        return RedirectResponse(url="https://www.google.com", status_code=302)
    db.add(EmailEvent(
        track_id=lid, email=email or link.email, campaign_id=campaign_id or link.campaign_id,
        event_type="click",
        ip=req.client.host if req.client else "",
        user_agent=req.headers.get("user-agent", "")[:500],
    ))
    db.commit()
    return RedirectResponse(url=link.url, status_code=302)


# ─── LISTE ──────────────────────────────────────────────────────────────────
@app.get("/lists", response_class=HTMLResponse)
def lists_ui(req: Request = Depends(require_auth)):
    return HTMLResponse(base_head("Liste") + header("lists") + """
<script src="https://unpkg.com/htmx.org@1.9.12"></script>
<div class="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">

<div>
<h1 class="text-2xl font-bold text-slate-900">Gestione Liste</h1>
<p class="text-slate-500 text-sm mt-1">Organizza i tuoi contatti in liste tematiche</p>
</div>

<div class="card p-6">
<h2 class="text-lg font-semibold text-slate-900 mb-4 flex items-center gap-2">
<svg class="w-5 h-5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
Crea Nuova Lista
</h2>
<form hx-post="/api/list" hx-target="#list-table" hx-swap="innerHTML" hx-on::after-request="this.reset()" class="grid grid-cols-1 md:grid-cols-3 gap-4">
<div><label class="block text-sm font-medium text-slate-700 mb-1.5">Nome Lista *</label>
<input name="name" placeholder="Es: Clienti VIP" required
class="w-full px-3 py-2.5 border border-slate-300 rounded-lg"></div>
<div class="md:col-span-2"><label class="block text-sm font-medium text-slate-700 mb-1.5">Descrizione (opzionale)</label>
<input name="description" placeholder="Descrizione della lista"
class="w-full px-3 py-2.5 border border-slate-300 rounded-lg"></div>
<button type="submit" class="md:col-span-3 btn-primary text-white py-2.5 rounded-lg font-semibold transition shadow-sm hover:shadow">
Crea Lista
</button>
</form>
</div>

<div id="list-table" hx-get="/api/lists" hx-trigger="load" class="card overflow-hidden"></div>
</div>
</body></html>""")


@app.get("/api/lists/json")
def get_lists_json(req: Request = Depends(require_auth), db: Session = Depends(get_db)):
    return [
        {"id": l.id, "name": l.name, "description": l.description or "", "count": len(l.recipients)}
        for l in db.query(RecipientList).order_by(RecipientList.name).all()
    ]


@app.get("/api/lists")
def get_lists_html(req: Request = Depends(require_auth), db: Session = Depends(get_db)):
    lists = db.query(RecipientList).order_by(RecipientList.name).all()
    if not lists:
        return HTMLResponse("""<div class="p-12 text-center">
<svg class="w-12 h-12 mx-auto mb-3 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/></svg>
<p class="text-slate-500">Nessuna lista creata</p>
<p class="text-slate-400 text-sm mt-1">Crea la tua prima lista usando il form qui sopra</p>
</div>""")

    rows = ""
    for l in lists:
        rows += f"""<tr class="border-b border-slate-100 hover:bg-slate-50 transition">
<td class="p-4">
<div class="font-semibold text-slate-900">{l.name}</div>
<div class="text-sm text-slate-500">{l.description or '—'}</div>
</td>
<td class="p-4">
<span class="inline-flex items-center gap-1.5 px-2.5 py-1 bg-indigo-50 text-indigo-700 rounded-full text-xs font-semibold">
<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
{len(l.recipients)} contatti
</span>
</td>
<td class="p-4 text-right">
<div class="flex justify-end gap-2">
<a href="/contacts?list_id={l.id}" class="inline-flex items-center gap-1 bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-1.5 rounded-lg text-sm font-medium transition">
<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
Vedi
</a>
<button hx-delete="/api/list/{l.id}" hx-target="#list-table" hx-confirm="Eliminare la lista '{l.name}'? I contatti non saranno cancellati."
class="inline-flex items-center gap-1 bg-red-50 hover:bg-red-100 text-red-700 px-3 py-1.5 rounded-lg text-sm font-medium transition">
<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M1 7h22M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3"/></svg>
Elimina
</button>
</div>
</td>
</tr>"""
    return HTMLResponse(f"""<table class="w-full">
<thead class="bg-slate-50 border-b border-slate-200"><tr>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Lista</th>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Contatti</th>
<th class="p-4 text-right text-xs font-semibold text-slate-600 uppercase tracking-wide">Azioni</th>
</tr></thead><tbody>{rows}</tbody></table>""")


@app.post("/api/list")
def create_list(req: Request = Depends(require_auth), db: Session = Depends(get_db),
                name: str = Form(...), description: str = Form(None)):
    if db.query(RecipientList).filter(RecipientList.name == name).first():
        return HTMLResponse("<div class='p-4 text-center text-red-600'>⚠️ Lista già esistente</div>")
    db.add(RecipientList(name=name, description=description or ""))
    db.commit()
    return get_lists_html(req, db)


@app.delete("/api/list/{lid}")
def del_list(lid: int, req: Request = Depends(require_auth), db: Session = Depends(get_db)):
    l = db.query(RecipientList).filter(RecipientList.id == lid).first()
    if l:
        db.delete(l)
        db.commit()
    return get_lists_html(req, db)


# ─── CONTATTI ───────────────────────────────────────────────────────────────
@app.get("/contacts", response_class=HTMLResponse)
def contacts_ui(req: Request = Depends(require_auth), message: str = Query(None)):
    banner = ""
    if message:
        is_err = "❌" in message
        cls = "bg-red-50 border-red-200 text-red-700" if is_err else "bg-emerald-50 border-emerald-200 text-emerald-700"
        banner = f'<div class="mb-4 px-4 py-3 rounded-lg border {cls} text-sm">{message}</div>'

    return HTMLResponse(base_head("Contatti") + header("contacts") + f"""
<script src="https://unpkg.com/htmx.org@1.9.12"></script>
<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">

<div class="flex items-center justify-between">
<div>
<h1 class="text-2xl font-bold text-slate-900">Contatti</h1>
<p class="text-slate-500 text-sm mt-1">Gestisci i destinatari delle tue email</p>
</div>
<a href="/api/sample-csv" class="inline-flex items-center gap-2 bg-amber-50 hover:bg-amber-100 text-amber-700 px-4 py-2 rounded-lg text-sm font-medium transition">
<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
Scarica CSV di esempio
</a>
</div>

{banner}

<div class="grid grid-cols-1 lg:grid-cols-3 gap-6">

<!-- Form aggiungi singolo contatto -->
<div class="card p-6">
<h2 class="text-lg font-semibold text-slate-900 mb-4 flex items-center gap-2">
<svg class="w-5 h-5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z"/></svg>
Aggiungi Contatto
</h2>
<form hx-post="/api/contact" hx-target="#contact-table" hx-swap="innerHTML" hx-encoding="multipart/form-data" hx-on::after-request="this.reset()" class="space-y-3">
<div><label class="block text-xs font-medium text-slate-700 mb-1">Email *</label>
<input name="email" type="email" required class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"></div>
<div class="grid grid-cols-2 gap-2">
<div><label class="block text-xs font-medium text-slate-700 mb-1">Nome</label>
<input name="nome" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"></div>
<div><label class="block text-xs font-medium text-slate-700 mb-1">Cognome</label>
<input name="cognome" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"></div>
</div>
<div class="grid grid-cols-2 gap-2">
<div><label class="block text-xs font-medium text-slate-700 mb-1">Var 1</label>
<input name="var1" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"></div>
<div><label class="block text-xs font-medium text-slate-700 mb-1">Var 2</label>
<input name="var2" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"></div>
</div>
<div class="grid grid-cols-2 gap-2">
<div><label class="block text-xs font-medium text-slate-700 mb-1">Var 3</label>
<input name="var3" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"></div>
<div><label class="block text-xs font-medium text-slate-700 mb-1">Var 4</label>
<input name="var4" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"></div>
</div>
<div><label class="block text-xs font-medium text-slate-700 mb-1">Var 5</label>
<input name="var5" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"></div>
<div><label class="block text-xs font-medium text-slate-700 mb-1">Lista *</label>
<select name="list_id" required class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm bg-white">
<option value="">Seleziona...</option>
</select></div>
<div class="border-t border-slate-100 pt-3">
<label class="block text-xs font-medium text-slate-700 mb-1">Allegato personale (opzionale)</label>
<input type="file" name="personal_file" class="w-full text-xs file:mr-2 file:py-1.5 file:px-3 file:rounded file:border-0 file:bg-slate-100 file:text-slate-700 hover:file:bg-slate-200">
</div>
<button type="submit" class="w-full btn-primary text-white py-2.5 rounded-lg font-semibold transition shadow-sm">
Salva Contatto
</button>
</form>
</div>

<!-- Lista contatti + import -->
<div class="lg:col-span-2 card p-6">
<div class="flex items-center justify-between mb-4">
<h2 class="text-lg font-semibold text-slate-900 flex items-center gap-2">
<svg class="w-5 h-5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/></svg>
Importa CSV
</h2>
<span id="list-badge" class="hidden text-xs bg-indigo-50 text-indigo-700 px-3 py-1 rounded-full font-medium">Filtro lista attivo</span>
</div>
<form action="/api/contacts/import" method="post" enctype="multipart/form-data" class="mb-6">
<label for="csv-file" class="block border-2 border-dashed border-slate-300 hover:border-indigo-400 rounded-lg p-8 text-center cursor-pointer transition">
<input type="file" name="file" accept=".csv" class="hidden" id="csv-file" required>
<svg class="w-10 h-10 mx-auto text-slate-400 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/></svg>
<div class="font-semibold text-slate-700">Clicca o trascina qui il file CSV</div>
<div id="csv-filename" class="text-xs text-slate-400 mt-2">Formato: email, nome, cognome, var1, var2, var3, var4, var5</div>
</label>
<div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-4">
<div><label class="block text-xs font-medium text-slate-700 mb-1">Assegna a Lista *</label>
<select name="list_id" required class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm bg-white">
<option value="">Seleziona...</option>
</select></div>
<div class="flex items-end">
<button type="submit" class="w-full btn-primary text-white py-2 rounded-lg font-semibold transition shadow-sm">
Importa Contatti
</button>
</div>
</div>
</form>

<div class="overflow-x-auto rounded-lg border border-slate-200">
<table class="w-full text-sm">
<thead class="bg-slate-50"><tr>
<th class="p-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">Email</th>
<th class="p-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">Nome</th>
<th class="p-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">Cognome</th>
<th class="p-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">Var 1</th>
<th class="p-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">Var 2</th>
<th class="p-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">Var 3</th>
<th class="p-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">Var 4</th>
<th class="p-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">Var 5</th>
<th class="p-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">Azioni</th>
</tr></thead>
<tbody id="contact-table"></tbody>
</table>
</div>
</div>

</div>
</div>

<script>
// Carica le liste nelle dropdown
fetch('/api/lists/json').then(r => r.json()).then(lists => {{
  document.querySelectorAll('select[name="list_id"]').forEach(sel => {{
    sel.innerHTML = '<option value="">Seleziona...</option>';
    lists.forEach(l => {{
      const o = document.createElement('option');
      o.value = l.id; o.textContent = `${{l.name}} (${{l.count}})`;
      sel.appendChild(o);
    }});
  }});
}});

// Mostra nome del file CSV selezionato
document.getElementById('csv-file').addEventListener('change', function(e) {{
  if (e.target.files[0]) {{
    document.getElementById('csv-filename').textContent = '📎 ' + e.target.files[0].name;
  }}
}});

// Filtro lista
const urlParams = new URLSearchParams(window.location.search);
const listId = urlParams.get('list_id');
if (listId) document.getElementById('list-badge').classList.remove('hidden');

function loadContacts(lid) {{
  let url = '/api/contacts';
  if (lid) url += '?list_id=' + encodeURIComponent(lid);
  fetch(url).then(r => r.text()).then(h => document.getElementById('contact-table').innerHTML = h);
}}
loadContacts(listId);

document.body.addEventListener('htmx:afterSwap', e => {{
  if (e.target.id === 'contact-table') loadContacts(listId);
}});
</script>
</body></html>""")


@app.post("/api/contact")
async def add_contact(req: Request = Depends(require_auth), db: Session = Depends(get_db),
                      email: str = Form(...), nome: str = Form(""), cognome: str = Form(""),
                      var1: str = Form(""), var2: str = Form(""), var3: str = Form(""),
                      var4: str = Form(""), var5: str = Form(""),
                      list_id: str = Form(None), personal_file: UploadFile = File(None)):
    if db.query(Recipient).filter(Recipient.email == email).first():
        return HTMLResponse("<tr><td colspan='9' class='p-4 text-center text-red-600 text-sm'>⚠️ Email già presente nel database</td></tr>")
    attachment_filename = ""
    if personal_file and personal_file.filename:
        os.makedirs("uploads/personalized", exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in personal_file.filename)
        # Aggiungi prefisso univoco per evitare collisioni
        safe_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
        with open(os.path.join("uploads", "personalized", safe_name), "wb") as buffer:
            buffer.write(await personal_file.read())
        attachment_filename = safe_name
    rec = Recipient(
        email=email, nome=nome, cognome=cognome,
        var1=var1, var2=var2, var3=var3, var4=var4, var5=var5,
        attachment_filename=attachment_filename,
    )
    if list_id:
        lst = db.query(RecipientList).filter(RecipientList.id == int(list_id)).first()
        if lst:
            rec.lists.append(lst)
    db.add(rec)
    db.commit()
    return get_contacts_html(req, db, list_id)


@app.post("/api/contacts/import")
async def import_contacts(req: Request = Depends(require_auth), db: Session = Depends(get_db),
                          file: UploadFile = File(...), list_id: str = Form(None)):
    try:
        content = await file.read()
        text = None
        for encoding in ["utf-8-sig", "utf-8", "cp1252", "latin-1"]:
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not text:
            text = content.decode("utf-8", errors="ignore")

        text = text.replace("\x00", "").strip()
        if not text:
            return RedirectResponse(url="/contacts?message=❌ File vuoto", status_code=303)

        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        lines = [line for line in lines if line.strip()]
        if not lines:
            return RedirectResponse(url="/contacts?message=❌ File vuoto", status_code=303)

        # Auto-detect del separatore
        sample = lines[0]
        if sample.count(";") > sample.count(","):
            reader = csv.reader(lines, delimiter=";")
        else:
            reader = csv.reader(lines)

        target_list = None
        if list_id:
            target_list = db.query(RecipientList).filter(RecipientList.id == int(list_id)).first()

        added, skipped, errors = 0, 0, 0
        first_row = True
        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            if first_row:
                first_col = row[0].strip().lower()
                if first_col in ["email", "mail", "indirizzo", "e-mail"]:
                    first_row = False
                    continue
                first_row = False

            email = row[0].strip() if len(row) > 0 else ""
            if not email or "@" not in email:
                errors += 1
                continue

            if db.query(Recipient).filter(Recipient.email == email).first():
                skipped += 1
                continue

            try:
                rec = Recipient(
                    email=email,
                    nome=row[1].strip() if len(row) > 1 else "",
                    cognome=row[2].strip() if len(row) > 2 else "",
                    var1=row[3].strip() if len(row) > 3 else "",
                    var2=row[4].strip() if len(row) > 4 else "",
                    var3=row[5].strip() if len(row) > 5 else "",
                    var4=row[6].strip() if len(row) > 6 else "",
                    var5=row[7].strip() if len(row) > 7 else "",
                )
                if target_list:
                    rec.lists.append(target_list)
                db.add(rec)
                added += 1
            except Exception:
                errors += 1

        db.commit()
        msg_parts = [f"✅ {added} contatti importati"]
        if skipped:
            msg_parts.append(f"{skipped} duplicati saltati")
        if errors:
            msg_parts.append(f"{errors} errori")
        return RedirectResponse(url=f"/contacts?message={', '.join(msg_parts)}", status_code=303)
    except Exception as e:
        import traceback
        logging.error(f"❌ Errore import: {traceback.format_exc()}")
        return RedirectResponse(url=f"/contacts?message=❌ Errore: {str(e)[:80]}", status_code=303)


@app.get("/api/contacts")
def get_contacts_html(req: Request = Depends(require_auth), db: Session = Depends(get_db), list_id: str = Query(None)):
    try:
        query = db.query(Recipient)
        if list_id and list_id.strip():
            lid = int(list_id)
            subquery = (
                db.query(recipient_list_association.c.recipient_id)
                .filter(recipient_list_association.c.list_id == lid).subquery()
            )
            query = query.filter(Recipient.id.in_(subquery))
        users = query.order_by(Recipient.email).all()
        if not users:
            return HTMLResponse("""<tr><td colspan="9" class="p-8 text-center text-slate-500 text-sm">
Nessun contatto presente. Aggiungine uno usando il form a sinistra o importa un CSV.
</td></tr>""")
        rows = []
        for u in users:
            is_unsub = u.status == "unsubscribed"
            row_class = "border-b border-slate-100 hover:bg-slate-50 transition"
            if is_unsub:
                row_class += " bg-red-50/40"

            attach = '<svg class="inline w-3 h-3 text-slate-400 ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"/></svg>' if u.attachment_filename else ""
            row = f"<tr class='{row_class}'>"
            row += f"<td class='p-3 font-medium text-slate-900'>{u.email}{attach}</td>"
            row += f"<td class='p-3 text-slate-600'>{u.nome or '—'}</td><td class='p-3 text-slate-600'>{u.cognome or '—'}</td>"
            for v in [u.var1, u.var2, u.var3, u.var4, u.var5]:
                v = v or ""
                t = (v[:18] + "…") if len(v) > 18 else (v or "—")
                row += f"<td class='p-3 text-slate-500 text-xs' title='{v}'>{t}</td>"

            if is_unsub:
                row += "<td class='p-3'><span class='inline-flex items-center gap-1 text-xs bg-red-100 text-red-700 px-2 py-1 rounded font-medium'>Disiscritto</span></td>"
            else:
                row += f"""<td class='p-3'>
<button hx-delete='/api/contact/{u.id}' hx-target='#contact-table' hx-swap='innerHTML' hx-confirm='Eliminare {u.email}?'
class='inline-flex items-center justify-center w-7 h-7 bg-red-50 hover:bg-red-100 text-red-600 rounded transition' title='Elimina'>
<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M1 7h22M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3"/></svg>
</button></td>"""
            row += "</tr>"
            rows.append(row)
        return HTMLResponse("".join(rows))
    except Exception as e:
        logging.error(f"💥 Errore contatti: {e}")
        return HTMLResponse("<tr><td colspan='9' class='p-4 text-center text-red-500'>Errore caricamento</td></tr>")


@app.delete("/api/contact/{uid}")
def del_contact(uid: int, req: Request = Depends(require_auth), db: Session = Depends(get_db)):
    u = db.query(Recipient).filter(Recipient.id == uid).first()
    if u:
        if u.attachment_filename:
            p = os.path.join("uploads", "personalized", u.attachment_filename)
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        db.delete(u)
        db.commit()
    return get_contacts_html(req, db)


# ─── TEMPLATE LIBRARY ───────────────────────────────────────────────────────
@app.get("/templates-list", response_class=HTMLResponse)
def templates_list_ui(req: Request = Depends(require_auth)):
    return HTMLResponse(base_head("Libreria Template") + header("templates-list") + """
<script src="https://unpkg.com/htmx.org@1.9.12"></script>
<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">

<div class="flex items-center justify-between">
<div>
<h1 class="text-2xl font-bold text-slate-900">Libreria Template</h1>
<p class="text-slate-500 text-sm mt-1">Tutti i template salvati pronti all'uso</p>
</div>
<a href="/templates" class="btn-primary inline-flex items-center gap-2 text-white px-4 py-2 rounded-lg text-sm font-semibold transition shadow-sm">
<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
Crea Nuovo Template
</a>
</div>

<div class="card overflow-hidden">
<table class="w-full text-sm">
<thead class="bg-slate-50 border-b border-slate-200"><tr>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Nome</th>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Oggetto</th>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Allegato</th>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Usato in</th>
<th class="p-4 text-right text-xs font-semibold text-slate-600 uppercase tracking-wide">Azioni</th>
</tr></thead>
<tbody id="tpls-table"></tbody>
</table>
</div>
</div>

<script>
function loadTpls() {
  fetch('/api/templates-list').then(r => r.json()).then(data => {
    const tbody = document.getElementById('tpls-table');
    if (!data.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="p-12 text-center"><svg class="w-12 h-12 mx-auto mb-3 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg><p class="text-slate-500">Nessun template salvato</p><p class="text-slate-400 text-sm mt-1">Crea il tuo primo template per iniziare</p></td></tr>';
      return;
    }
    tbody.innerHTML = data.map(t => `<tr class="border-b border-slate-100 hover:bg-slate-50 transition">
<td class="p-4 font-semibold text-slate-900">${t.name}</td>
<td class="p-4 text-slate-600">${t.subject}</td>
<td class="p-4">${t.has_attachment ? '<span class="inline-flex items-center gap-1 text-xs bg-amber-50 text-amber-700 px-2 py-1 rounded"><svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"/></svg>Sì</span>' : '<span class="text-slate-400 text-xs">—</span>'}</td>
<td class="p-4"><span class="inline-flex items-center text-xs bg-slate-100 text-slate-700 px-2 py-1 rounded">${t.usage_count} campagne</span></td>
<td class="p-4 text-right"><div class="flex justify-end gap-2">
<button onclick="location.href='/templates?edit=${t.id}'" class="inline-flex items-center gap-1 bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-1.5 rounded-lg text-xs font-medium transition" title="Modifica"><svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>Modifica</button>
<button onclick="location.href='/schedule?template_id=${t.id}'" class="inline-flex items-center gap-1 bg-emerald-50 hover:bg-emerald-100 text-emerald-700 px-3 py-1.5 rounded-lg text-xs font-medium transition" title="Usa"><svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>Usa</button>
<button onclick="if(confirm('Eliminare ${t.name.replace(/'/g, "\\\\'")}?')) fetch('/api/template/${t.id}',{method:'DELETE'}).then(()=>loadTpls())" class="inline-flex items-center gap-1 bg-red-50 hover:bg-red-100 text-red-700 px-3 py-1.5 rounded-lg text-xs font-medium transition" title="Elimina"><svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M1 7h22M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3"/></svg>Elimina</button>
</div></td></tr>`).join('');
  });
}
loadTpls();
</script>
</body></html>""")


@app.get("/api/templates-list")
def get_templates_list(req: Request = Depends(require_auth), db: Session = Depends(get_db)):
    return [
        {
            "id": t.id, "name": t.name, "subject": t.subject,
            "has_attachment": bool(t.attachment_path),
            "usage_count": db.query(Campaign).filter(Campaign.template_id == t.id).count(),
        }
        for t in db.query(Template).order_by(Template.name).all()
    ]


# ─── TEMPLATE EDITOR ────────────────────────────────────────────────────────
@app.get("/templates", response_class=HTMLResponse)
def tpl_ui(req: Request = Depends(require_auth)):
    return HTMLResponse(base_head("Editor Template") + header("templates") + """
<link href="https://cdn.jsdelivr.net/npm/summernote@0.8.18/dist/summernote-lite.min.css" rel="stylesheet">
<script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/summernote@0.8.18/dist/summernote-lite.min.js"></script>

<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">

<div>
<h1 class="text-2xl font-bold text-slate-900">Editor Template</h1>
<p class="text-slate-500 text-sm mt-1">Crea o modifica template HTML per le tue email</p>
</div>

<!-- Galleria predefiniti -->
<div class="card p-6">
<h2 class="text-lg font-semibold text-slate-900 mb-4 flex items-center gap-2">
<svg class="w-5 h-5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 21a4 4 0 01-4-4V5a2 2 0 012-2h4a2 2 0 012 2v12a4 4 0 01-4 4zm0 0h12a2 2 0 002-2v-4a2 2 0 00-2-2h-2.343M11 7.343l1.657-1.657a2 2 0 012.828 0l2.829 2.829a2 2 0 010 2.828l-8.486 8.485M7 17h.01"/></svg>
Template Predefiniti
</h2>
<p class="text-sm text-slate-500 mb-4">Inizia da un design pronto e personalizzalo</p>
<div id="template-gallery" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
<div class="text-center text-slate-500 py-8 col-span-4">Caricamento...</div>
</div>
</div>

<!-- Editor -->
<div class="card p-6 space-y-4">
<h2 class="text-lg font-semibold text-slate-900 flex items-center gap-2">
<svg class="w-5 h-5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
Crea / Modifica Template
</h2>
<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
<div><label class="block text-sm font-medium text-slate-700 mb-1.5">Nome Template *</label>
<input id="tplName" placeholder="Es: Newsletter Maggio" class="w-full px-3 py-2.5 border border-slate-300 rounded-lg"></div>
<div><label class="block text-sm font-medium text-slate-700 mb-1.5">Oggetto Email *</label>
<input id="tplSubject" placeholder="Es: Le novità del mese" class="w-full px-3 py-2.5 border border-slate-300 rounded-lg"></div>
</div>

<div class="bg-slate-50 rounded-lg p-3">
<div class="text-sm font-medium text-slate-700 mb-2">Inserisci variabili dinamiche:</div>
<div class="flex flex-wrap gap-2">
<button type="button" onclick="insertVar('{{ nome }}')" class="bg-white hover:bg-indigo-50 border border-slate-200 text-slate-700 px-3 py-1.5 rounded text-xs font-medium transition">{{ nome }}</button>
<button type="button" onclick="insertVar('{{ cognome }}')" class="bg-white hover:bg-indigo-50 border border-slate-200 text-slate-700 px-3 py-1.5 rounded text-xs font-medium transition">{{ cognome }}</button>
<button type="button" onclick="insertVar('{{ email }}')" class="bg-white hover:bg-indigo-50 border border-slate-200 text-slate-700 px-3 py-1.5 rounded text-xs font-medium transition">{{ email }}</button>
<button type="button" onclick="insertVar('{{ variabile1 }}')" class="bg-white hover:bg-indigo-50 border border-slate-200 text-slate-700 px-3 py-1.5 rounded text-xs font-medium transition">{{ variabile1 }}</button>
<button type="button" onclick="insertVar('{{ variabile2 }}')" class="bg-white hover:bg-indigo-50 border border-slate-200 text-slate-700 px-3 py-1.5 rounded text-xs font-medium transition">{{ variabile2 }}</button>
<button type="button" onclick="insertVar('{{ variabile3 }}')" class="bg-white hover:bg-indigo-50 border border-slate-200 text-slate-700 px-3 py-1.5 rounded text-xs font-medium transition">{{ variabile3 }}</button>
<button type="button" onclick="insertVar('{{ variabile4 }}')" class="bg-white hover:bg-indigo-50 border border-slate-200 text-slate-700 px-3 py-1.5 rounded text-xs font-medium transition">{{ variabile4 }}</button>
<button type="button" onclick="insertVar('{{ variabile5 }}')" class="bg-white hover:bg-indigo-50 border border-slate-200 text-slate-700 px-3 py-1.5 rounded text-xs font-medium transition">{{ variabile5 }}</button>
</div>
</div>

<textarea id="summernote"></textarea>

<div class="bg-amber-50 border border-amber-200 rounded-lg p-4">
<label class="block text-sm font-semibold text-amber-900 mb-2 flex items-center gap-2">
<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"/></svg>
Allegato (opzionale)
</label>
<p class="text-xs text-amber-700 mb-2">Verrà allegato a tutte le email inviate con questo template</p>
<input type="file" id="tplFile" class="text-sm file:mr-2 file:py-1.5 file:px-3 file:rounded file:border-0 file:bg-amber-100 file:text-amber-700 hover:file:bg-amber-200">
<div id="currentAttach" class="hidden mt-2 text-xs text-amber-700"></div>
</div>

<div class="flex gap-3 pt-2">
<button type="button" onclick="saveTemplate()" class="flex-1 btn-primary text-white py-2.5 rounded-lg font-semibold transition shadow-sm">Salva Template</button>
<button type="button" onclick="previewTemplate()" class="flex-1 bg-slate-100 hover:bg-slate-200 text-slate-700 py-2.5 rounded-lg font-semibold transition">Anteprima</button>
</div>
<div id="msg" class="hidden text-sm text-center p-3 rounded-lg"></div>
<div id="preview" class="hidden mt-4 p-4 border border-slate-200 rounded-lg bg-slate-50">
<div class="text-xs text-slate-500 mb-2 font-semibold">ANTEPRIMA</div>
<div id="preview-content" class="bg-white p-4 rounded shadow-sm"></div>
</div>
</div>
</div>

<script>
$(document).ready(function() {
  $('#summernote').summernote({
    placeholder: 'Scrivi qui il contenuto della tua email...',
    tabsize: 2, height: 400,
    toolbar: [
      ['style', ['style']],
      ['font', ['bold', 'italic', 'underline', 'clear']],
      ['color', ['color']],
      ['para', ['ul', 'ol', 'paragraph']],
      ['table', ['table']],
      ['insert', ['link', 'picture']],
      ['view', ['fullscreen', 'codeview', 'help']]
    ]
  });

  // Carica galleria predefiniti
  fetch('/api/templates/premade').then(r => r.json()).then(templates => {
    const gallery = document.getElementById('template-gallery');
    gallery.innerHTML = '';
    Object.values(templates).forEach(t => {
      const card = document.createElement('div');
      card.className = 'border border-slate-200 rounded-lg p-4 hover:border-indigo-400 hover:shadow-md transition cursor-pointer group';
      card.onclick = () => loadTemplate(t.id);
      card.innerHTML = `<div class="font-semibold text-slate-900 mb-1 group-hover:text-indigo-600 transition">${t.name}</div><div class="text-xs text-slate-500 mb-3">${t.preview}</div><button class="w-full bg-slate-100 group-hover:bg-indigo-600 group-hover:text-white text-slate-700 py-1.5 rounded text-xs font-semibold transition">Usa questo template</button>`;
      gallery.appendChild(card);
    });
  });

  // Caricamento per modifica
  const params = new URLSearchParams(window.location.search);
  if (params.get('edit')) {
    fetch(`/api/template/${params.get('edit')}`).then(r => r.json()).then(t => {
      if (t && t.id) {
        document.getElementById('tplName').value = t.name;
        document.getElementById('tplSubject').value = t.subject;
        $('#summernote').summernote('code', t.html_content);
        if (t.has_attachment) {
          const el = document.getElementById('currentAttach');
          el.textContent = '📎 Allegato corrente: ' + t.attachment_filename;
          el.classList.remove('hidden');
        }
        window.editTplId = t.id;
      }
    });
  }
});

function insertVar(tag) { $('#summernote').summernote('insertText', tag + ' '); }

function loadTemplate(id) {
  fetch('/api/templates/premade').then(r => r.json()).then(templates => {
    const t = templates[id];
    if (t) {
      $('#summernote').summernote('code', t.html);
      if (!document.getElementById('tplName').value)
        document.getElementById('tplName').value = t.name + ' - Personalizzato';
      if (!document.getElementById('tplSubject').value)
        document.getElementById('tplSubject').value = 'Oggetto per ' + t.name;
    }
  });
}

function showMsg(text, isErr = false) {
  const el = document.getElementById('msg');
  el.textContent = text;
  el.className = 'text-sm text-center p-3 rounded-lg ' + (isErr ? 'bg-red-50 text-red-700' : 'bg-emerald-50 text-emerald-700');
  setTimeout(() => el.classList.add('hidden'), 4000);
}

function saveTemplate() {
  const name = document.getElementById('tplName').value.trim();
  const subject = document.getElementById('tplSubject').value.trim();
  const content = $('#summernote').summernote('code');
  if (!name || !subject || !content) { showMsg('⚠️ Compila nome, oggetto e contenuto', true); return; }

  const formData = new FormData();
  formData.append('name', name);
  formData.append('subject', subject);
  formData.append('html_content', content);
  if (window.editTplId) formData.append('template_id', window.editTplId);

  const fileInput = document.getElementById('tplFile');
  if (fileInput.files[0]) formData.append('file', fileInput.files[0]);

  fetch('/api/template', { method: 'POST', body: formData }).then(r => {
    if (r.redirected) { window.location.href = r.url; }
    else if (r.ok) { showMsg('✅ Template salvato!'); setTimeout(() => location.href = '/templates-list', 800); }
    else { r.text().then(t => showMsg(t, true)); }
  }).catch(e => showMsg('❌ ' + e, true));
}

function previewTemplate() {
  const html = $('#summernote').summernote('code');
  document.getElementById('preview-content').innerHTML = html;
  document.getElementById('preview').classList.remove('hidden');
  document.getElementById('preview').scrollIntoView({behavior: 'smooth'});
}
</script>
</body></html>""")


@app.get("/api/templates/premade")
def get_premade_templates(req: Request = Depends(require_auth)):
    return {t["id"]: t for t in PREMADE_TEMPLATES}


@app.get("/api/templates/json")
def get_tpls_json(req: Request = Depends(require_auth), db: Session = Depends(get_db)):
    return [{"id": t.id, "name": t.name, "subject": t.subject} for t in db.query(Template).order_by(Template.name).all()]


@app.get("/api/template/{tid}")
def get_template_detail(tid: int, req: Request = Depends(require_auth), db: Session = Depends(get_db)):
    t = db.query(Template).filter(Template.id == tid).first()
    if not t:
        return {}
    return {
        "id": t.id, "name": t.name, "subject": t.subject,
        "html_content": t.html_content,
        "has_attachment": bool(t.attachment_path),
        "attachment_filename": os.path.basename(t.attachment_path) if t.attachment_path else "",
    }


@app.post("/api/template")
async def create_or_update_tpl(req: Request = Depends(require_auth), db: Session = Depends(get_db),
                               name: str = Form(...), subject: str = Form(...), html_content: str = Form(...),
                               template_id: str = Form(None), file: UploadFile = File(None)):
    try:
        existing = None
        if template_id and template_id.strip():
            existing = db.query(Template).filter(Template.id == int(template_id)).first()

        attachment_path = existing.attachment_path if existing else ""
        if file and file.filename:
            os.makedirs("uploads", exist_ok=True)
            safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in file.filename)
            safe_name = f"tpl_{uuid.uuid4().hex[:8]}_{safe_name}"
            new_path = os.path.join("uploads", safe_name)
            with open(new_path, "wb") as buffer:
                buffer.write(await file.read())
            # Rimuovi il vecchio se esiste
            if existing and existing.attachment_path and os.path.exists(existing.attachment_path):
                try:
                    os.remove(existing.attachment_path)
                except Exception:
                    pass
            attachment_path = new_path

        if existing:
            existing.name = name
            existing.subject = subject
            existing.html_content = html_content
            existing.attachment_path = attachment_path
        else:
            db.add(Template(name=name, subject=subject, html_content=html_content, attachment_path=attachment_path))
        db.commit()
        return RedirectResponse(url="/templates-list", status_code=303)
    except Exception as e:
        db.rollback()
        return HTMLResponse(f"<div class='p-4 text-center text-red-600'>❌ {e}</div>")


@app.delete("/api/template/{tid}")
def del_tpl(tid: int, req: Request = Depends(require_auth), db: Session = Depends(get_db)):
    t = db.query(Template).filter(Template.id == tid).first()
    if t:
        if t.attachment_path and os.path.exists(t.attachment_path):
            try:
                os.remove(t.attachment_path)
            except Exception:
                pass
        db.delete(t)
        db.commit()
    return JSONResponse({"ok": True})


# ─── CAMPAGNE / SCHEDULE ────────────────────────────────────────────────────
@app.get("/schedule", response_class=HTMLResponse)
def schedule_ui(req: Request = Depends(require_auth)):
    return HTMLResponse(base_head("Pianifica Campagne") + header("schedule") + """
<link href="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.10/index.global.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.10/index.global.min.js"></script>

<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">

<div>
<h1 class="text-2xl font-bold text-slate-900">Pianifica Campagne</h1>
<p class="text-slate-500 text-sm mt-1">Programma l'invio automatico delle tue email</p>
</div>

<!-- Form nuova campagna -->
<div class="card p-6">
<h2 class="text-lg font-semibold text-slate-900 mb-4 flex items-center gap-2">
<svg class="w-5 h-5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
Nuova Campagna
</h2>
<form id="camp-form" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
<div><label class="block text-sm font-medium text-slate-700 mb-1.5">Nome Campagna *</label>
<input name="name" placeholder="Es: Newsletter Maggio" required class="w-full px-3 py-2.5 border border-slate-300 rounded-lg"></div>
<div><label class="block text-sm font-medium text-slate-700 mb-1.5">Lista *</label>
<select name="list_id" id="list-select" required class="w-full px-3 py-2.5 border border-slate-300 rounded-lg bg-white">
<option value="">Caricamento...</option>
</select></div>
<div><label class="block text-sm font-medium text-slate-700 mb-1.5">Template *</label>
<select name="template_id" id="tpl-select" required class="w-full px-3 py-2.5 border border-slate-300 rounded-lg bg-white">
<option value="">Caricamento...</option>
</select></div>
<div><label class="block text-sm font-medium text-slate-700 mb-1.5">Data/Ora Invio *</label>
<input name="scheduled_at" type="datetime-local" required class="w-full px-3 py-2.5 border border-slate-300 rounded-lg"></div>
<button type="submit" class="md:col-span-2 lg:col-span-4 btn-primary text-white py-2.5 rounded-lg font-semibold transition shadow-sm">
Crea Campagna
</button>
</form>
<div id="camp-msg" class="hidden mt-3 text-sm text-center p-3 rounded-lg"></div>
</div>

<!-- Test invio -->
<div class="card p-6 bg-gradient-to-br from-amber-50 to-orange-50 border-amber-200">
<h3 class="font-semibold text-amber-900 mb-2 flex items-center gap-2">
<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
Invio Immediato (Test)
</h3>
<p class="text-sm text-amber-800 mb-3">Forza l'invio immediato di una campagna pianificata, ignorando la data programmata</p>
<div class="flex gap-3">
<select id="debug-campaign-select" class="flex-1 px-3 py-2 border border-amber-300 rounded-lg bg-white">
<option value="">Caricamento...</option>
</select>
<button onclick="sendDebug()" class="bg-amber-600 hover:bg-amber-700 text-white px-6 py-2 rounded-lg font-semibold transition shadow-sm">
Invia Ora
</button>
</div>
<div id="debug-result" class="mt-3 text-sm"></div>
</div>

<!-- Calendario -->
<div class="card p-6">
<h3 class="font-semibold text-slate-900 mb-4 flex items-center gap-2">
<svg class="w-5 h-5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
Calendario Campagne
</h3>
<div id="calendar"></div>
</div>
</div>

<script>
let calendar;

function loadAll() {
  fetch('/api/lists/json').then(r => r.json()).then(l => {
    const sel = document.getElementById('list-select');
    sel.innerHTML = '<option value="">Seleziona Lista...</option>';
    l.forEach(x => { const o = document.createElement('option'); o.value = x.id; o.textContent = `${x.name} (${x.count})`; sel.appendChild(o); });
  });
  fetch('/api/templates/json').then(r => r.json()).then(t => {
    const sel = document.getElementById('tpl-select');
    sel.innerHTML = '<option value="">Seleziona Template...</option>';
    t.forEach(x => { const o = document.createElement('option'); o.value = x.id; o.textContent = x.name; sel.appendChild(o); });
    const params = new URLSearchParams(window.location.search);
    if (params.get('template_id')) sel.value = params.get('template_id');
  });
  fetch('/api/campaigns').then(r => r.json()).then(c => {
    const sel = document.getElementById('debug-campaign-select');
    sel.innerHTML = '<option value="">Seleziona campagna...</option>';
    c.forEach(x => { const o = document.createElement('option'); o.value = x.id; o.textContent = x.title; sel.appendChild(o); });
  });
}

document.getElementById('camp-form').addEventListener('submit', async function(e) {
  e.preventDefault();
  const formData = new FormData(this);
  const r = await fetch('/api/campaign', { method: 'POST', body: formData });
  const msg = document.getElementById('camp-msg');
  if (r.ok) {
    msg.textContent = '✅ Campagna creata correttamente';
    msg.className = 'mt-3 text-sm text-center p-3 rounded-lg bg-emerald-50 text-emerald-700';
    msg.classList.remove('hidden');
    this.reset();
    if (calendar) calendar.refetchEvents();
    loadAll();
  } else {
    const t = await r.text();
    msg.textContent = '❌ ' + t;
    msg.className = 'mt-3 text-sm text-center p-3 rounded-lg bg-red-50 text-red-700';
    msg.classList.remove('hidden');
  }
  setTimeout(() => msg.classList.add('hidden'), 4000);
});

document.addEventListener('DOMContentLoaded', function() {
  loadAll();
  const el = document.getElementById('calendar');
  calendar = new FullCalendar.Calendar(el, {
    initialView: 'dayGridMonth',
    headerToolbar: { left: 'prev,next today', center: 'title', right: 'dayGridMonth,timeGridWeek' },
    events: '/api/campaigns',
    locale: 'it',
    firstDay: 1,
    height: 600,
    eventClick: function(info) {
      if (info.event.id) document.getElementById('debug-campaign-select').value = info.event.id;
    }
  });
  calendar.render();
});

function sendDebug() {
  const cid = document.getElementById('debug-campaign-select').value;
  const res = document.getElementById('debug-result');
  if (!cid) { res.innerHTML = '<span class="text-red-600">⚠️ Seleziona una campagna</span>'; return; }
  res.innerHTML = '<span class="text-amber-700">⏳ Invio in corso...</span>';
  fetch('/api/debug/send-campaign/' + cid, { method: 'POST' }).then(r => r.json()).then(d => {
    res.innerHTML = d.message ? `<span class="text-emerald-700 font-medium">${d.message}</span>` : `<span class="text-red-600">❌ ${d.detail || 'Errore'}</span>`;
    if (calendar) calendar.refetchEvents();
  });
}
</script>
</body></html>""")


@app.post("/api/campaign")
def create_campaign(req: Request = Depends(require_auth), db: Session = Depends(get_db),
                    name: str = Form(...), list_id: int = Form(...), template_id: int = Form(...),
                    scheduled_at: str = Form(...)):
    try:
        dt = datetime.fromisoformat(scheduled_at)
        c = Campaign(name=name, list_id=list_id, template_id=template_id, scheduled_at=dt, status="scheduled")
        db.add(c)
        db.commit()
        db.refresh(c)
        return JSONResponse({"ok": True, "id": c.id})
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))


@app.get("/api/campaigns")
def get_campaigns(req: Request = Depends(require_auth), db: Session = Depends(get_db)):
    camps = db.query(Campaign).all()
    color_map = {
        "scheduled": "#6366f1",
        "running": "#f59e0b",
        "completed": "#10b981",
        "failed": "#ef4444",
        "draft": "#94a3b8",
    }
    return [
        {
            "title": c.name,
            "start": c.scheduled_at.isoformat(),
            "id": c.id,
            "color": color_map.get(c.status, "#6366f1"),
        }
        for c in camps
    ]


@app.post("/api/debug/send-campaign/{campaign_id}")
def debug_send_campaign(campaign_id: int, req: Request = Depends(require_auth)):
    result = send_campaign_now(campaign_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return JSONResponse({"message": f"✅ Inviate {result['sent']} email"})


# ─── CRONOLOGIA ─────────────────────────────────────────────────────────────
@app.get("/campaigns", response_class=HTMLResponse)
def campaigns_history(req: Request = Depends(require_auth)):
    return HTMLResponse(base_head("Cronologia") + header("campaigns") + """
<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">

<div>
<h1 class="text-2xl font-bold text-slate-900">Cronologia Campagne</h1>
<p class="text-slate-500 text-sm mt-1">Storico di tutte le campagne inviate o pianificate</p>
</div>

<div class="card overflow-hidden">
<table class="w-full text-sm">
<thead class="bg-slate-50 border-b border-slate-200"><tr>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Campagna</th>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Lista</th>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Template</th>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Data</th>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Stato</th>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Inviate</th>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Aperture</th>
</tr></thead>
<tbody id="campaign-table"></tbody>
</table>
</div>
</div>

<script>
const STATUS_BADGE = {
  scheduled:   '<span class="inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold bg-indigo-50 text-indigo-700"><span class="w-1.5 h-1.5 rounded-full bg-indigo-500"></span>Pianificata</span>',
  running:     '<span class="inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold bg-amber-50 text-amber-700"><span class="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse"></span>In corso</span>',
  completed:   '<span class="inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>Completata</span>',
  failed:      '<span class="inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold bg-red-50 text-red-700"><span class="w-1.5 h-1.5 rounded-full bg-red-500"></span>Fallita</span>',
  draft:       '<span class="inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold bg-slate-100 text-slate-600"><span class="w-1.5 h-1.5 rounded-full bg-slate-400"></span>Bozza</span>'
};

fetch('/api/campaigns/history').then(r => r.json()).then(data => {
  const tbody = document.getElementById('campaign-table');
  if (!data.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="p-12 text-center"><svg class="w-12 h-12 mx-auto mb-3 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg><p class="text-slate-500">Nessuna campagna ancora</p><p class="text-slate-400 text-sm mt-1"><a href="/schedule" class="text-indigo-600 hover:underline">Pianifica la prima campagna</a></p></td></tr>';
    return;
  }
  tbody.innerHTML = data.map(c => `<tr class="border-b border-slate-100 hover:bg-slate-50 cursor-pointer transition" onclick="location.href='/campaign-detail?campaign_id=${c.id}'">
    <td class="p-4 font-semibold text-indigo-600 hover:text-indigo-800">${c.name}</td>
    <td class="p-4 text-slate-600">${c.list_name}</td>
    <td class="p-4 text-slate-600">${c.template_name}</td>
    <td class="p-4 text-slate-500 text-xs">${c.scheduled_at}</td>
    <td class="p-4">${STATUS_BADGE[c.status] || c.status}</td>
    <td class="p-4 font-medium text-slate-700">${c.sent_count}</td>
    <td class="p-4 font-medium text-slate-700">${c.opens_count}</td>
  </tr>`).join('');
});
</script>
</body></html>""")


@app.get("/api/campaigns/history")
def get_campaigns_history(req: Request = Depends(require_auth), db: Session = Depends(get_db)):
    camps = (
        db.query(Campaign)
        .join(RecipientList, Campaign.list_id == RecipientList.id)
        .join(Template, Campaign.template_id == Template.id)
        .order_by(Campaign.scheduled_at.desc())
        .all()
    )
    out = []
    for c in camps:
        sent_count = db.query(CampaignLog).filter(CampaignLog.campaign_id == c.id, CampaignLog.status == "sent").count()
        opens_count = db.query(EmailEvent).filter(EmailEvent.campaign_id == c.id, EmailEvent.event_type == "open").count()
        out.append({
            "id": c.id, "name": c.name,
            "list_name": c.list.name, "template_name": c.template.name,
            "scheduled_at": c.scheduled_at.strftime("%d/%m/%Y %H:%M"),
            "status": c.status,
            "sent_count": sent_count,
            "opens_count": opens_count,
        })
    return out


# ─── DETTAGLIO CAMPAGNA ─────────────────────────────────────────────────────
@app.get("/campaign-detail", response_class=HTMLResponse)
def campaign_detail_ui(req: Request = Depends(require_auth)):
    return HTMLResponse(base_head("Dettaglio Campagna") + header() + """
<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">

<a href="/campaigns" class="inline-flex items-center gap-2 text-slate-600 hover:text-slate-900 text-sm font-medium">
<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"/></svg>
Torna alla cronologia
</a>

<div class="card p-6">
<h1 class="text-2xl font-bold text-slate-900" id="camp-title">Caricamento...</h1>
<p class="text-slate-500 text-sm mt-1">Statistiche dettagliate dei destinatari</p>

<div class="grid grid-cols-2 md:grid-cols-4 gap-4 mt-6">
<div class="bg-slate-50 rounded-lg p-4">
<div class="text-xs font-semibold text-slate-500 uppercase tracking-wide">Destinatari</div>
<div class="text-2xl font-bold text-slate-900 mt-1" id="stat-total">—</div>
</div>
<div class="bg-emerald-50 rounded-lg p-4">
<div class="text-xs font-semibold text-emerald-700 uppercase tracking-wide">Inviate</div>
<div class="text-2xl font-bold text-emerald-700 mt-1" id="stat-ok">—</div>
</div>
<div class="bg-sky-50 rounded-lg p-4">
<div class="text-xs font-semibold text-sky-700 uppercase tracking-wide">Aperte</div>
<div class="text-2xl font-bold text-sky-700 mt-1" id="stat-opened">—</div>
</div>
<div class="bg-amber-50 rounded-lg p-4">
<div class="text-xs font-semibold text-amber-700 uppercase tracking-wide">Tasso apertura</div>
<div class="text-2xl font-bold text-amber-700 mt-1" id="stat-rate">—</div>
</div>
</div>
</div>

<div class="card overflow-hidden">
<table class="w-full text-sm">
<thead class="bg-slate-50 border-b border-slate-200"><tr>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Nome</th>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Cognome</th>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Email</th>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Inviata il</th>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Stato</th>
<th class="p-4 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">Prima apertura</th>
</tr></thead>
<tbody id="detail-table"></tbody>
</table>
</div>
</div>

<script>
const params = new URLSearchParams(window.location.search);
const cid = params.get('campaign_id');
if (!cid) location.href = '/campaigns';

fetch(`/api/campaign/${cid}/details`).then(r => r.json()).then(data => {
  if (data.error) { document.getElementById('camp-title').textContent = data.error; return; }
  document.getElementById('camp-title').textContent = data.campaign_name;
  const total = data.recipients.length;
  const sent = data.recipients.filter(r => r.status === 'sent').length;
  const opened = data.recipients.filter(r => r.opened_at).length;
  document.getElementById('stat-total').textContent = total;
  document.getElementById('stat-ok').textContent = sent;
  document.getElementById('stat-opened').textContent = opened;
  document.getElementById('stat-rate').textContent = sent > 0 ? ((opened / sent) * 100).toFixed(1) + '%' : '0%';

  const tbody = document.getElementById('detail-table');
  if (!data.recipients.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="p-8 text-center text-slate-500">Nessun destinatario per questa campagna.</td></tr>';
    return;
  }
  tbody.innerHTML = data.recipients.map(r => `<tr class="border-b border-slate-100 hover:bg-slate-50 transition">
    <td class="p-4 text-slate-700">${r.nome || '—'}</td>
    <td class="p-4 text-slate-700">${r.cognome || '—'}</td>
    <td class="p-4 font-medium text-slate-900">${r.email}</td>
    <td class="p-4 text-slate-500 text-xs">${r.sent_at}</td>
    <td class="p-4">${r.status === 'sent'
      ? '<span class="inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>Inviata</span>'
      : '<span class="inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold bg-red-50 text-red-700"><span class="w-1.5 h-1.5 rounded-full bg-red-500"></span>Errore</span>'}</td>
    <td class="p-4 text-xs ${r.opened_at ? 'text-sky-700 font-medium' : 'text-slate-400'}">${r.opened_at || '— Non aperta —'}</td>
  </tr>`).join('');
}).catch(e => console.error('Errore:', e));
</script>
</body></html>""")


@app.get("/api/campaign/{cid}/details")
def get_campaign_details(cid: int, req: Request = Depends(require_auth), db: Session = Depends(get_db)):
    camp = db.query(Campaign).filter(Campaign.id == cid).first()
    if not camp:
        return {"error": "Campagna non trovata"}

    logs = (
        db.query(CampaignLog)
        .join(Recipient, CampaignLog.recipient_id == Recipient.id)
        .filter(CampaignLog.campaign_id == cid)
        .order_by(CampaignLog.sent_at)
        .all()
    )

    recipients = []
    for log in logs:
        # Cerca la prima apertura usando il track_id (se disponibile) o l'email
        first_open = None
        if log.track_id:
            first_open = (
                db.query(EmailEvent)
                .filter(EmailEvent.track_id == log.track_id, EmailEvent.event_type == "open")
                .order_by(EmailEvent.timestamp.asc())
                .first()
            )
        if not first_open:
            first_open = (
                db.query(EmailEvent)
                .filter(
                    EmailEvent.email == log.recipient.email,
                    EmailEvent.campaign_id == cid,
                    EmailEvent.event_type == "open",
                )
                .order_by(EmailEvent.timestamp.asc())
                .first()
            )

        recipients.append({
            "nome": log.recipient.nome,
            "cognome": log.recipient.cognome,
            "email": log.recipient.email,
            "sent_at": log.sent_at.strftime("%d/%m/%Y %H:%M:%S"),
            "status": log.status,
            "opened_at": first_open.timestamp.strftime("%d/%m/%Y %H:%M") if first_open else None,
        })

    return {"campaign_name": camp.name, "status": camp.status, "recipients": recipients}
