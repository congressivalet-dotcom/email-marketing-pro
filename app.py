import os, csv, io, logging, shutil, json
from datetime import datetime
from urllib.parse import quote
from fastapi import FastAPI, Request, Form, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import Response, HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from database import init_db, get_db
from models import Recipient, RecipientList, EmailEvent, Campaign, Template, CampaignLog, recipient_list_association
from email_service import send_email
from scheduler import start_scheduler, stop_scheduler, send_campaign_now

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s | %(levelname)s | %(message)s")
app = FastAPI(title="📧 Email Marketing Pro")

SECRET = os.getenv("SESSION_SECRET", "test-secret-32chars")
app.add_middleware(SessionMiddleware, secret_key=SECRET, https_only=False, same_site="lax")
ADMIN_USER = os.getenv("DASHBOARD_USER", "admin")
ADMIN_PASS = os.getenv("DASHBOARD_PASSWORD", "admin123")
PIXEL = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"

PREMADE_TEMPLATES = [
    {"id": "minimal", "name": "📄 Minimal", "preview": "Design pulito", "html": "<div style='font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px;background:#fff;border:1px solid #e2e8f0;'><h2 style='color:#1e293b'>Ciao {{ nome }},</h2><p style='color:#64748b;line-height:1.6'>{{ variabile1 }}</p></div>"},
    {"id": "corporate", "name": "💼 Corporate", "preview": "Business professionale", "html": "<div style='font-family:Arial,sans-serif;max-width:600px;margin:auto;background:#fff;border:1px solid #e2e8f0;'><div style='background:#1e3a8a;color:#fff;padding:20px;text-align:center;'><h1 style='margin:0'>{{ variabile1 }}</h1></div><div style='padding:30px 20px;'><p style='color:#1e293b'>Gentile {{ nome }} {{ cognome }},</p><p style='color:#64748b'>{{ variabile2 }}</p></div></div>"},
    {"id": "promo", "name": "🎁 Promo", "preview": "Offerte e sconti", "html": "<div style='font-family:Arial,sans-serif;max-width:600px;margin:auto;background:#fff;border:2px solid #1e3a8a;'><div style='background:#1e3a8a;color:#fff;padding:25px;text-align:center;'><h1 style='margin:0'>{{ variabile1 }}</h1></div><div style='padding:30px;'><p style='color:#1e293b'>Ciao {{ nome }},</p><p style='color:#64748b'>{{ variabile2 }}</p></div></div>"},
    {"id": "newsletter", "name": "📰 Newsletter", "preview": "Aggiornamenti periodici", "html": "<div style='font-family:Arial,sans-serif;max-width:600px;margin:auto;background:#fff;border-top:4px solid #1e3a8a;'><div style='padding:20px;text-align:center;border-bottom:1px solid #e2e8f0;'><h1 style='color:#1e3a8a;margin:0'>{{ variabile1 }}</h1></div><div style='padding:20px;'><p style='color:#1e293b'>Ciao {{ nome }},</p><p style='color:#64748b'>{{ variabile2 }}</p></div></div>"}
]

def get_header(current_page: str = ""):
    nav_items = [
        ("📊 Dashboard", "/", current_page == "dashboard"),
        ("📦 Liste", "/lists", current_page == "lists"),
        ("👥 Contatti", "/contacts", current_page == "contacts"),
        ("📝 Template", "/templates", current_page == "templates"),
        ("📋 Library", "/templates-list", current_page == "templates-list"),
        ("📅 Campagne", "/schedule", current_page == "schedule"),
        ("📜 Cronologia", "/campaigns", current_page == "campaigns"),
    ]
    nav_html = "".join(f'<a href="{url}" class="px-3 py-1.5 rounded-lg text-sm font-medium transition {"bg-white/20 text-white" if active else "text-slate-300 hover:bg-white/10"}">{label}</a>' for label, url, active in nav_items)
    return f"""<div class="bg-slate-900 text-white shadow-lg sticky top-0 z-50 border-b border-slate-700">
  <div class="max-w-7xl mx-auto px-6 py-3">
    <div class="flex justify-between items-center">
      <div class="flex items-center space-x-3"><div class="text-2xl">📧</div><div><h1 class="text-lg font-bold">Email Marketing Pro</h1></div></div>
      <div class="flex items-center space-x-1">{nav_html}
        <form method="post" action="/logout" class="ml-2"><button class="px-3 py-1.5 rounded-lg text-sm font-medium transition bg-red-600 hover:bg-red-700">🚪 Esci</button></form>
      </div>
    </div>
  </div>
</div>"""

@app.on_event("startup")
def startup(): 
    os.makedirs("uploads", exist_ok=True); os.makedirs("uploads/personalized", exist_ok=True); os.makedirs("test_emails", exist_ok=True)
    init_db(); start_scheduler(); logging.info("✅ Sistema pronto")
@app.on_event("shutdown")
def shutdown(): stop_scheduler()

async def require_auth(req: Request):
    if not req.session.get("auth"): return RedirectResponse(url="/login", status_code=303)

LOGIN_HTML = """<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Accesso</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-slate-100 min-h-screen flex items-center justify-center p-4">
<div class="bg-white rounded-xl shadow-xl p-8 w-full max-w-md border border-slate-200">
  <div class="text-center mb-8"><div class="text-4xl mb-3">📧</div><h1 class="text-2xl font-bold text-slate-800">Email Marketing Pro</h1><p class="text-slate-500 mt-2">Accedi per continuare</p></div>
  <form method="post" action="/login" class="space-y-4">
    <div><label class="block text-sm font-medium text-slate-700 mb-1">Username</label><input name="username" placeholder="admin" class="w-full px-4 py-2.5 border border-slate-300 rounded-lg" required></div>
    <div><label class="block text-sm font-medium text-slate-700 mb-1">Password</label><input name="password" type="password" placeholder="••••••••" class="w-full px-4 py-2.5 border border-slate-300 rounded-lg" required></div>
    <button type="submit" class="w-full bg-slate-800 text-white py-2.5 rounded-lg font-semibold hover:bg-slate-900 transition">🔐 Accedi</button>
  </form>
</div></body></html>"""

@app.get("/login")
def login_form(): return HTMLResponse(LOGIN_HTML)
@app.post("/login")
def login_proc(req: Request, username: str=Form(...), password: str=Form(...)):
    if username == ADMIN_USER and password == ADMIN_PASS:
        req.session["auth"] = True; req.session["user"] = username
        return RedirectResponse(url="/", status_code=303)
    return HTMLResponse(LOGIN_HTML.replace("</form>",'<p class="text-red-600 text-center mt-4">❌ Credenziali errate</p></form>'), status_code=401)
@app.post("/logout")
def logout(req: Request): req.session.clear(); return RedirectResponse(url="/login", status_code=303)

DASH = get_header("dashboard") + """
<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://unpkg.com/htmx.org@1.9.12"></script>
</head><body class="bg-slate-50 min-h-screen">
<div class="max-w-7xl mx-auto px-6 py-8 space-y-6">
  <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
    <button hx-post="/api/test" hx-target="#log" class="bg-white p-6 rounded-xl shadow border border-slate-200 hover:shadow-lg transition text-left">
      <div class="text-3xl mb-2">📤</div><div class="font-bold text-slate-800">Invia Test</div><div class="text-slate-500 text-sm">Invia email di prova</div>
    </button>
    <a href="/api/export" class="bg-white p-6 rounded-xl shadow border border-slate-200 hover:shadow-lg transition text-left">
      <div class="text-3xl mb-2">📥</div><div class="font-bold text-slate-800">Export CSV</div><div class="text-slate-500 text-sm">Scarica dati eventi</div>
    </a>
    <button id="refreshChart" class="bg-white p-6 rounded-xl shadow border border-slate-200 hover:shadow-lg transition text-left">
      <div class="text-3xl mb-2">📈</div><div class="font-bold text-slate-800">Aggiorna Grafico</div><div class="text-slate-500 text-sm">Ricarga statistiche</div>
    </button>
  </div>
  <div class="bg-white rounded-xl shadow border border-slate-200 p-6">
    <h2 class="text-lg font-bold text-slate-800 mb-4 flex items-center"><span class="text-xl mr-2">📊</span> Statistiche</h2>
    <div class="h-80"><canvas id="chart"></canvas></div>
  </div>
  <div class="bg-slate-900 rounded-xl shadow p-6">
    <h2 class="text-sm font-bold text-slate-300 mb-3">💻 Log Operazioni</h2>
    <div id="log" class="bg-slate-800 rounded-lg p-4 font-mono text-sm text-green-400 h-32 overflow-auto">Pronto...</div>
  </div>
</div>
<script>
const ctx=document.getElementById('chart').getContext('2d');
const chartData = { 
    type: 'line', 
     { 
        labels: [], 
        datasets: [
            { label: 'Aperture',  [], borderColor: '#1e3a8a', backgroundColor: 'rgba(30, 58, 138, 0.1)', fill: true, tension: 0.3 }, 
            { label: 'Click',  [], borderColor: '#10b981', backgroundColor: 'rgba(16, 185, 129, 0.1)', fill: true, tension: 0.3 }
        ] 
    }, 
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: true } }, scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } } } 
};
const c = new Chart(ctx, chartData);
async function updateChart(){ try{ const r = await fetch('/api/stats'); const d = await r.json(); c.data.labels = d.dates; c.data.datasets[0].data = d.opens; c.data.datasets[1].data = d.clicks; c.update(); }catch(e){ console.error('Errore grafico:', e); } }
document.getElementById('refreshChart').addEventListener('click', updateChart); setInterval(updateChart, 30000); updateChart();
document.body.addEventListener('htmx:afterSwap', e => { if(e.target.id === 'log') e.target.scrollTop = e.target.scrollHeight; });
</script></body></html>"""

@app.get("/", response_class=HTMLResponse)
def dashboard(req: Request=Depends(require_auth)): return HTMLResponse(DASH)

@app.post("/api/test")
def api_test(req: Request=Depends(require_auth)):
    try:
        ok = send_email("test@prova.it", "Test Track", "<h1>Ciao</h1><p>Apri & clicca.</p><a href='https://example.com'>Link</a>")
        return HTMLResponse("✅ Email inviata!" if ok else "❌ Errore invio.", status_code=200 if ok else 500)
    except Exception as e: return HTMLResponse(f"❌ Errore: {e}", status_code=500)

@app.get("/api/stats")
def api_stats(req: Request=Depends(require_auth), db: Session=Depends(get_db)):
    evts = db.query(EmailEvent).all()
    o, cl = {}, {}
    for e in evts:
        d = e.timestamp.strftime("%Y-%m-%d")
        if e.event_type=="open": o[d]=o.get(d,0)+1
        else: cl[d]=cl.get(d,0)+1
    dates = sorted(set(list(o.keys())+list(cl.keys())))
    return {"dates":dates, "opens":[o.get(d,0) for d in dates], "clicks":[cl.get(d,0) for d in dates]}

@app.get("/api/export")
def api_export(req: Request=Depends(require_auth), db: Session=Depends(get_db)):
    evts = db.query(EmailEvent).order_by(EmailEvent.timestamp.desc()).all()
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["id","track_id","email","type","time","ip","user_agent"])
    for e in evts: w.writerow([e.id, e.track_id, e.email, e.event_type, e.timestamp.isoformat(), e.ip or "", e.user_agent or ""])
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv", headers={"Content-Disposition":"attachment; filename=events.csv"})

# ✅ FIX: Messaggio Unsubscribe aggiornato
@app.get("/unsubscribe")
def unsubscribe(email: str = Query(...), db: Session=Depends(get_db)):
    rec = db.query(Recipient).filter(Recipient.email == email).first()
    if rec:
        rec.status = "unsubscribed"
        db.commit()
        return HTMLResponse("""
        <div class="text-center py-12 px-4" style="font-family: sans-serif;">
            <div class="text-5xl mb-4">👋</div>
            <h2 class="text-2xl font-bold text-slate-800 mb-4">Ci dispiace vederti andare via!</h2>
            <p class="text-slate-600 mb-4 max-w-lg mx-auto">
                Siamo dispiaciuti che hai deciso di abbandonarci. Potrai comunque iscriverti nuovamente nel caso in cui tornerai ad essere interessato alle nostre proposte.
            </p>
            <p class="text-slate-500 italic">Grazie del tempo che ci hai dedicato.</p>
        </div>
        """)
    return HTMLResponse("<div style='font-family:sans-serif;text-align:center;padding:40px;'><h2>⚠️ Email non trovata</h2></div>")

@app.get("/track/open/{tid}")
def track_open(tid: str, req: Request, db: Session=Depends(get_db)):
    db.add(EmailEvent(track_id=tid, event_type="open", ip=req.client.host or "-", user_agent=req.headers.get("user-agent","-")))
    db.commit()
    return Response(content=PIXEL, media_type="image/gif", headers={"Cache-Control":"no-store"})

@app.get("/track/click/{lid}")
def track_click(lid: str, url: str=Query(...), req: Request=..., db: Session=Depends(get_db)):
    db.add(EmailEvent(track_id=lid, event_type="click", ip=req.client.host or "-", user_agent=req.headers.get("user-agent","-")))
    db.commit()
    return RedirectResponse(url=url, status_code=302)

LISTS_HTML = get_header("lists") + """
<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Liste</title><script src="https://cdn.tailwindcss.com"></script><script src="https://unpkg.com/htmx.org@1.9.12"></script></head>
<body class="bg-slate-50 min-h-screen">
<div class="max-w-6xl mx-auto px-6 py-8 space-y-6">
  <div class="bg-white rounded-xl shadow border border-slate-200 p-8">
    <h2 class="text-xl font-bold text-slate-800 mb-6">➕ Crea Nuova Lista</h2>
    <form hx-post="/api/list" hx-target="#list-table" hx-swap="innerHTML" hx-on::after-request="this.reset()" class="space-y-4">
      <div><label class="block text-sm font-medium text-slate-700 mb-2">Nome Lista *</label><input name="name" placeholder="Es: Clienti VIP" class="w-full px-4 py-2.5 border border-slate-300 rounded-lg" required></div>
      <div><label class="block text-sm font-medium text-slate-700 mb-2">Descrizione</label><input name="description" placeholder="Descrizione opzionale" class="w-full px-4 py-2.5 border border-slate-300 rounded-lg"></div>
      <button type="submit" class="w-full bg-slate-800 text-white py-2.5 rounded-lg font-semibold hover:bg-slate-900 transition">💾 Salva Lista</button>
    </form>
  </div>
  <div id="list-table" hx-get="/api/lists" hx-trigger="load" class="bg-white rounded-xl shadow border border-slate-200 overflow-hidden"></div>
</div></body></html>"""

@app.get("/lists", response_class=HTMLResponse)
def lists_ui(req: Request=Depends(require_auth)): return HTMLResponse(LISTS_HTML)
@app.get("/api/lists/json")
def get_lists_json(req: Request=Depends(require_auth), db: Session=Depends(get_db)):
    return [{"id": l.id, "name": l.name, "description": l.description, "count": len(l.recipients)} for l in db.query(RecipientList).all()]
@app.get("/api/lists")
def get_lists_html(req: Request=Depends(require_auth), db: Session=Depends(get_db)):
    lists = db.query(RecipientList).all()
    if not lists: return HTMLResponse("<div class='p-8 text-center text-slate-500'>📭 Nessuna lista creata</div>")
    rows = "".join(f"<tr class='border-b hover:bg-slate-50'><td class='p-4'><div class='font-semibold'>{l.name}</div><div class='text-sm text-slate-500'>{l.description or '-'}</div></td><td class='p-4'><span class='bg-slate-100 text-slate-800 px-3 py-1 rounded-full text-sm'>{len(l.recipients)} contatti</span></td><td class='p-4'><div class='flex space-x-2'><a href='/contacts?list_id={l.id}' class='bg-slate-600 hover:bg-slate-700 text-white px-4 py-2 rounded-lg text-sm'>👁️ Vedi</a><button hx-delete='/api/list/{l.id}' hx-target='#list-table' class='bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded-lg text-sm'>🗑️ Elimina</button></div></td></tr>" for l in lists)
    return HTMLResponse(f"<table class='w-full'><thead class='bg-slate-50'><tr><th class='p-4 text-left text-sm font-semibold'>Lista</th><th class='p-4 text-left text-sm font-semibold'>Contatti</th><th class='p-4 text-left text-sm font-semibold'>Azioni</th></tr></thead><tbody>{rows}</tbody></table>")
@app.post("/api/list")
def create_list(req: Request=Depends(require_auth), db: Session=Depends(get_db), name: str=Form(...), description: str=Form(None)):
    if db.query(RecipientList).filter(RecipientList.name==name).first(): return HTMLResponse("<div class='p-4 text-center text-red-600'>⚠️ Lista già esistente</div>")
    db.add(RecipientList(name=name, description=description)); db.commit()
    return get_lists_html(req, db)
@app.delete("/api/list/{lid}")
def del_list(lid: int, req: Request=Depends(require_auth), db: Session=Depends(get_db)):
    l = db.query(RecipientList).filter(RecipientList.id==lid).first()
    if l: db.delete(l); db.commit()
    return get_lists_html(req, db)

CONTACTS_HTML = get_header("contacts") + """
<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Contatti</title><script src="https://cdn.tailwindcss.com"></script><script src="https://unpkg.com/htmx.org@1.9.12"></script></head>
<body class="bg-slate-50 min-h-screen">
<div class="max-w-7xl mx-auto px-6 py-8 space-y-6">
  <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
    <div class="lg:col-span-1 bg-white rounded-xl shadow border border-slate-200 p-6 space-y-4">
      <h2 class="text-lg font-bold text-slate-800">➕ Aggiungi Contatto</h2>
      <form hx-post="/api/contact" hx-target="#contact-table" hx-swap="innerHTML" hx-encoding="multipart/form-data" hx-on::after-request="this.reset()" class="space-y-3">
        <div><label class="block text-xs font-medium text-slate-700 mb-1">Email *</label><input name="email" type="email" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm" required></div>
        <div class="grid grid-cols-2 gap-2"><div><label class="block text-xs font-medium text-slate-700 mb-1">Nome</label><input name="nome" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"></div><div><label class="block text-xs font-medium text-slate-700 mb-1">Cognome</label><input name="cognome" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"></div></div>
        <div class="grid grid-cols-2 gap-2"><div><label class="block text-xs font-medium text-slate-700 mb-1">Var1</label><input name="var1" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"></div><div><label class="block text-xs font-medium text-slate-700 mb-1">Var2</label><input name="var2" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"></div></div>
        <div class="grid grid-cols-2 gap-2"><div><label class="block text-xs font-medium text-slate-700 mb-1">Var3</label><input name="var3" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"></div><div><label class="block text-xs font-medium text-slate-700 mb-1">Var4</label><input name="var4" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"></div></div>
        <div><label class="block text-xs font-medium text-slate-700 mb-1">Var5</label><input name="var5" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"></div>
        <div><label class="block text-xs font-medium text-slate-700 mb-1">Lista *</label><select name="list_id" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm" required><option value="">Seleziona...</option></select></div>
        <div class="border-t pt-3"><label class="block text-xs font-medium text-slate-700 mb-1">📎 Allegato</label><input type="file" name="personal_file" class="w-full text-xs"></div>
        <button type="submit" class="w-full bg-slate-800 text-white py-2 rounded-lg font-medium hover:bg-slate-900 transition">💾 Salva</button>
      </form>
    </div>
    <div class="lg:col-span-2 bg-white rounded-xl shadow border border-slate-200 p-6">
      <div class="flex justify-between items-center mb-4">
        <h2 class="text-lg font-bold text-slate-800">📥 Importa CSV</h2>
        <span id="list-badge" class="text-xs bg-slate-100 text-slate-800 px-3 py-1 rounded-full hidden">Filtro attivo</span>
      </div>
      <form action="/api/contacts/import" method="post" enctype="multipart/form-data" class="mb-6">
        <div class="border-2 border-dashed border-slate-300 rounded-lg p-6 text-center">
          <input type="file" name="file" accept=".csv" class="hidden" id="csv-file">
          <label for="csv-file" class="cursor-pointer block">
            <div class="text-3xl mb-2">📂</div>
            <div class="font-semibold text-slate-700">Clicca o trascina qui il file CSV</div>
            <div class="text-xs text-slate-400 mt-2">Supporta header opzionale</div>
          </label>
        </div>
        <div class="mt-3"><label class="block text-xs font-medium text-slate-700 mb-1">Assegna a Lista *</label><select name="list_id" class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm" required><option value="">Seleziona...</option></select></div>
        <button type="submit" class="mt-2 w-full bg-slate-800 text-white py-2 rounded-lg font-medium hover:bg-slate-900 transition">🚀 Importa</button>
      </form>
      <div class="overflow-x-auto rounded-lg border border-slate-200">
        <table class="w-full text-sm">
          <thead class="bg-slate-50"><tr><th class="p-3 text-left font-semibold">Email</th><th class="p-3 text-left font-semibold">Nome</th><th class="p-3 text-left font-semibold">Cognome</th><th class="p-3 text-left font-semibold">Var1</th><th class="p-3 text-left font-semibold">Var2</th><th class="p-3 text-left font-semibold">Var3</th><th class="p-3 text-left font-semibold">Var4</th><th class="p-3 text-left font-semibold">Var5</th><th class="p-3 text-left font-semibold">Azioni</th></tr></thead>
          <tbody id="contact-table"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>
<script>
  fetch('/api/lists/json').then(r=>r.json()).then(l=>{
    document.querySelectorAll('select[name="list_id"]').forEach(sel=>{
      sel.innerHTML='<option value="">Seleziona...</option>';
      l.forEach(x=>{const o=document.createElement('option');o.value=x.id;o.textContent=x.name;sel.appendChild(o);});
    });
  });
  const urlParams=new URLSearchParams(window.location.search); const listId=urlParams.get('list_id');
  if(listId) document.getElementById('list-badge').classList.remove('hidden');
  function loadContacts(lid){ let url='/api/contacts'; if(lid)url+='?list_id='+encodeURIComponent(lid); fetch(url).then(r=>r.text()).then(h=>document.getElementById('contact-table').innerHTML=h); }
  if(listId)loadContacts(listId);else loadContacts(null);
  document.body.addEventListener('htmx:afterSwap',e=>{ if(e.target.id==='contact-table'){ if(listId)loadContacts(listId);else loadContacts(null); } });
</script></body></html>"""

@app.get("/contacts", response_class=HTMLResponse)
def contacts_ui(req: Request=Depends(require_auth), message: str = Query(None)):
    html = CONTACTS_HTML
    if message:
        banner_class = "bg-red-100 border-red-400 text-red-700" if "❌" in message else "bg-emerald-100 border-emerald-400 text-emerald-700"
        banner = f'<div class="max-w-7xl mx-auto px-6 pt-4"><div id="import-banner" class="{banner_class} border px-4 py-3 rounded-lg mb-4">{message}</div></div>'
        html = html.replace('<div class="max-w-7xl mx-auto px-6 py-8 space-y-6">', banner + '<div class="max-w-7xl mx-auto px-6 py-8 space-y-6">')
    return HTMLResponse(html)

@app.post("/api/contact")
async def add_contact(req: Request=Depends(require_auth), db: Session=Depends(get_db), 
                     email: str=Form(...), nome: str=Form(""), cognome: str=Form(""), 
                     var1: str=Form(""), var2: str=Form(""), var3: str=Form(""), var4: str=Form(""), var5: str=Form(""), 
                     list_id: str=Form(None), personal_file: UploadFile=File(None)):
    if db.query(Recipient).filter(Recipient.email==email).first(): return HTMLResponse("<tr><td colspan='9' class='p-4 text-center text-red-600'>⚠️ Email già presente</td></tr>")
    attachment_filename = ""
    if personal_file and personal_file.filename:
        os.makedirs("uploads/personalized", exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in personal_file.filename)
        with open(os.path.join("uploads", "personalized", safe_name), "wb") as buffer: buffer.write(await personal_file.read())
        attachment_filename = safe_name
    rec = Recipient(email=email, nome=nome, cognome=cognome, var1=var1, var2=var2, var3=var3, var4=var4, var5=var5, attachment_filename=attachment_filename)
    if list_id:
        lst = db.query(RecipientList).filter(RecipientList.id==int(list_id)).first()
        if lst: rec.lists.append(lst)
    db.add(rec); db.commit()
    return get_contacts_html(req, db, list_id)

@app.post("/api/contacts/import")
async def import_contacts(req: Request=Depends(require_auth), db: Session=Depends(get_db), file: UploadFile=File(...), list_id: str = Form(None)):
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
            text = content.decode('utf-8', errors='ignore')
        
        text = text.replace('\x00', '').strip()
        if not text:
            return RedirectResponse(url="/contacts?message=❌ File vuoto", status_code=303)
        
        lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        lines = [line for line in lines if line.strip()]
        if not lines:
            return RedirectResponse(url="/contacts?message=❌ File vuoto", status_code=303)
        
        reader = csv.reader(lines)
        added = 0
        skipped = 0
        first_row = True
        target_list = None
        if list_id:
            target_list = db.query(RecipientList).filter(RecipientList.id == int(list_id)).first()
        
        for row in reader:
            if not row or not any(cell.strip() for cell in row): continue
            if first_row:
                first_col = row[0].strip().lower()
                if first_col in ["email", "mail", "indirizzo", "e-mail"]:
                    first_row = False
                    continue
                first_row = False
            
            email = row[0].strip() if len(row) > 0 else ""
            if not email or "@" not in email:
                skipped += 1
                continue
            
            if db.query(Recipient).filter(Recipient.email==email).first():
                skipped += 1
                continue
            
            rec = Recipient(
                email=email,
                nome=row[1].strip() if len(row)>1 else "",
                cognome=row[2].strip() if len(row)>2 else "",
                var1=row[3].strip() if len(row)>3 else "",
                var2=row[4].strip() if len(row)>4 else "",
                var3=row[5].strip() if len(row)>5 else "",
                var4=row[6].strip() if len(row)>6 else "",
                var5=row[7].strip() if len(row)>7 else ""
            )
            if target_list: rec.lists.append(target_list)
            db.add(rec)
            added += 1
        
        db.commit()
        return RedirectResponse(url=f"/contacts?message=✅ {added} contatti importati, {skipped} saltati", status_code=303)
    except Exception as e:
        import traceback
        logging.error(f"❌ Errore import: {traceback.format_exc()}")
        return RedirectResponse(url=f"/contacts?message=❌ Errore: {str(e)[:80]}", status_code=303)

@app.get("/api/contacts")
def get_contacts_html(req: Request=Depends(require_auth), db: Session=Depends(get_db), list_id: str = Query(None)):
    try:
        query = db.query(Recipient)
        if list_id and list_id.strip():
            lid = int(list_id)
            subquery = db.query(recipient_list_association.c.recipient_id).filter(recipient_list_association.c.list_id == lid).subquery()
            query = query.filter(Recipient.id.in_(subquery))
        users = query.all()
        if not users: return HTMLResponse("<tr><td colspan='9' class='p-8 text-center text-slate-500'>📭 Nessun contatto</td></tr>")
        rows = []
        for u in users:
            is_unsubscribed = u.status == "unsubscribed"
            row_class = "border-b hover:bg-slate-50 transition"
            if is_unsubscribed:
                row_class += " bg-slate-100 text-slate-400 opacity-60 cursor-not-allowed"
            
            row = f"<tr class='{row_class}'>"
            row += f"<td class='p-3 font-medium'>{u.email}</td>"
            row += f"<td class='p-3'>{u.nome or '-'}</td><td class='p-3'>{u.cognome or '-'}</td>"
            for v in [u.var1, u.var2, u.var3, u.var4, u.var5]:
                t = str(v).strip()[:20] + "..." if len(str(v).strip()) > 20 else (v or '-')
                row += f"<td class='p-3' title='{v or ''}'>{t}</td>"
            
            if not is_unsubscribed:
                row += f"<td class='p-3'><button hx-delete='/api/contact/{u.id}' hx-target='#contact-table' hx-swap='innerHTML' hx-confirm='Eliminare questo contatto?' class='bg-red-600 hover:bg-red-700 text-white px-3 py-1 rounded-lg text-xs'>✕</button></td>"
            else:
                row += f"<td class='p-3'><span class='text-xs text-red-400 font-bold'>🚫 BANNATO</span></td>"
            row += "</tr>"
            rows.append(row)
        return HTMLResponse("".join(rows))
    except Exception as e:
        logging.error(f"💥 Errore contatti: {e}")
        return HTMLResponse("<tr><td colspan='9' class='p-4 text-center text-red-500'>Errore caricamento</td></tr>")

@app.delete("/api/contact/{uid}")
def del_contact(uid: int, req: Request=Depends(require_auth), db: Session=Depends(get_db)):
    u = db.query(Recipient).filter(Recipient.id==uid).first()
    if u:
        if u.attachment_filename:
            p = os.path.join("uploads", "personalized", u.attachment_filename)
            if os.path.exists(p): os.remove(p)
        db.delete(u); db.commit()
    return get_contacts_html(req, db)

TEMPLATES_LIST_HTML = get_header("templates-list") + """
<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Template Library</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-slate-50 min-h-screen">
<div class="max-w-7xl mx-auto px-6 py-8">
  <div class="bg-white rounded-xl shadow border border-slate-200 overflow-hidden">
    <div class="p-6 border-b flex justify-between items-center"><h2 class="text-xl font-bold">📋 Libreria Template</h2><a href="/templates" class="bg-slate-600 hover:bg-slate-700 text-white px-4 py-2 rounded-lg">➕ Crea Nuovo</a></div>
    <div class="overflow-x-auto"><table class="w-full text-sm"><thead class="bg-slate-50"><tr><th class="p-4 text-left font-semibold">Nome</th><th class="p-4 text-left font-semibold">Oggetto</th><th class="p-4 text-left font-semibold">Usato in</th><th class="p-4 text-left font-semibold">Azioni</th></tr></thead><tbody id="tpls-table"></tbody></table></div>
  </div>
</div>
<script>
fetch('/api/templates-list').then(r=>r.json()).then(d=>{
  document.getElementById('tpls-table').innerHTML=d.length?d.map(t=>`<tr class="border-b"><td class="p-4 font-medium">${t.name}</td><td class="p-4">${t.subject}</td><td class="p-4">${t.usage_count} campagne</td><td class="p-4 flex gap-2"><button onclick="location.href='/templates?edit=${t.id}'" class="bg-slate-600 text-white px-3 py-1 rounded text-xs">✏️</button><button onclick="location.href='/schedule?template_id=${t.id}'" class="bg-emerald-600 text-white px-3 py-1 rounded text-xs">🔄</button><button hx-delete='/api/template/${t.id}' hx-target='#tpls-table' class="bg-red-600 text-white px-3 py-1 rounded text-xs">🗑️</button></td></tr>`).join(''):'<tr><td colspan="4" class="p-8 text-center">Nessun template</td></tr>';
});
</script></body></html>"""

@app.get("/templates-list", response_class=HTMLResponse)
def templates_list_ui(req: Request=Depends(require_auth)): return HTMLResponse(TEMPLATES_LIST_HTML)
@app.get("/api/templates-list")
def get_templates_list(req: Request=Depends(require_auth), db: Session=Depends(get_db)):
    return [{"id": t.id, "name": t.name, "subject": t.subject, "usage_count": db.query(Campaign).filter(Campaign.template_id == t.id).count()} for t in db.query(Template).all()]

TPL_HTML = get_header("templates") + """
<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Template</title>
<link href="https://cdn.jsdelivr.net/npm/summernote@0.8.18/dist/summernote-lite.min.css" rel="stylesheet">
<script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/summernote@0.8.18/dist/summernote-lite.min.js"></script>
<script src="https://cdn.tailwindcss.com"></script>
</head><body class="bg-slate-50 min-h-screen">
<div class="max-w-7xl mx-auto px-6 py-8 space-y-6">
  <div class="bg-white rounded-xl shadow border border-slate-200 p-6">
    <h2 class="text-xl font-bold text-slate-800 mb-4">🎨 Template Predefiniti</h2>
    <div id="template-gallery" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4"><div class="text-center text-slate-500 py-8">Caricamento...</div></div>
  </div>
  <div class="bg-white rounded-xl shadow border border-slate-200 p-6 space-y-4">
    <h2 class="text-xl font-bold text-slate-800">✏️ Crea / Modifica Template</h2>
    <div class="grid grid-cols-2 gap-4">
      <div><label class="block text-sm font-medium text-slate-700 mb-2">Nome Template *</label><input name="name" id="tplName" placeholder="Es: Newsletter Gennaio" class="w-full px-4 py-2.5 border border-slate-300 rounded-lg"></div>
      <div><label class="block text-sm font-medium text-slate-700 mb-2">Oggetto Email *</label><input name="subject" id="tplSubject" placeholder="Es: Le novità del mese" class="w-full px-4 py-2.5 border border-slate-300 rounded-lg"></div>
    </div>
    <div class="bg-slate-50 rounded-lg p-3 mb-2">
      <span class="text-sm font-medium text-slate-700 mr-2">Inserisci Variabili:</span>
      <button type="button" onclick="insertVar('{{ nome }}')" class="bg-slate-600 hover:bg-slate-700 text-white px-3 py-1 rounded text-xs">👤 {{ nome }}</button>
      <button type="button" onclick="insertVar('{{ cognome }}')" class="bg-slate-600 hover:bg-slate-700 text-white px-3 py-1 rounded text-xs">👤 {{ cognome }}</button>
      <button type="button" onclick="insertVar('{{ email }}')" class="bg-slate-600 hover:bg-slate-700 text-white px-3 py-1 rounded text-xs">📧 {{ email }}</button>
      <button type="button" onclick="insertVar('{{ variabile1 }}')" class="bg-slate-600 hover:bg-slate-700 text-white px-3 py-1 rounded text-xs">📌 Var1</button>
      <button type="button" onclick="insertVar('{{ variabile2 }}')" class="bg-slate-600 hover:bg-slate-700 text-white px-3 py-1 rounded text-xs">📌 Var2</button>
      <button type="button" onclick="insertVar('{{ variabile3 }}')" class="bg-slate-600 hover:bg-slate-700 text-white px-3 py-1 rounded text-xs">📌 Var3</button>
      <button type="button" onclick="insertVar('{{ variabile4 }}')" class="bg-slate-600 hover:bg-slate-700 text-white px-3 py-1 rounded text-xs">📌 Var4</button>
      <button type="button" onclick="insertVar('{{ variabile5 }}')" class="bg-slate-600 hover:bg-slate-700 text-white px-3 py-1 rounded text-xs">📌 Var5</button>
    </div>
    <textarea id="summernote" name="html_content"></textarea>
    <div class="flex gap-4 pt-2">
      <button type="button" onclick="saveTemplate()" class="flex-1 bg-slate-800 text-white py-2.5 rounded-lg font-semibold hover:bg-slate-900 transition">✅ Salva Template</button>
      <button type="button" onclick="previewTemplate()" class="flex-1 bg-slate-600 text-white py-2.5 rounded-lg font-semibold hover:bg-slate-700 transition">👁️ Anteprima</button>
    </div>
    <div id="preview" class="hidden mt-4 p-4 border rounded-lg bg-slate-50"><div id="preview-content" class="bg-white p-4 rounded shadow-sm"></div></div>
  </div>
</div>
<script>
$(document).ready(function() {
  $('#summernote').summernote({ placeholder: 'Scrivi qui...', tabsize: 2, height: 400, toolbar: [['style', ['style']], ['font', ['bold', 'underline', 'clear']], ['color', ['color']], ['para', ['ul', 'ol', 'paragraph']], ['table', ['table']], ['insert', ['link', 'picture']], ['view', ['fullscreen', 'codeview', 'help']]] });
  fetch('/api/templates/premade').then(r=>r.json()).then(templates=>{
    const gallery=document.getElementById('template-gallery');
    gallery.innerHTML='';
    Object.values(templates).forEach(t=>{
      const card=document.createElement('div');
      card.className='border border-slate-200 rounded-lg p-4 hover:shadow-md transition cursor-pointer';
      card.onclick=()=>loadTemplate(t.id);
      card.innerHTML=`<div class="font-bold text-slate-800">${t.name}</div><div class="text-xs text-slate-500 mt-1">${t.preview}</div><button class="mt-3 w-full bg-slate-600 text-white py-1 rounded text-xs">Usa</button>`;
      gallery.appendChild(card);
    });
  }).catch(e=>console.error(e));
  const params=new URLSearchParams(window.location.search);
  if(params.get('edit')){
    fetch(`/api/template/${params.get('edit')}`).then(r=>r.json()).then(t=>{
      if(t){
        document.getElementById('tplName').value=t.name;
        document.getElementById('tplSubject').value=t.subject;
        $('#summernote').summernote('code', t.html_content);
      }
    });
  }
});
function insertVar(tag) { $('#summernote').summernote('insertText', tag + ' '); }
function loadTemplate(id) {
  fetch('/api/templates/premade').then(r=>r.json()).then(templates=>{
    const t=templates[id];
    if(t){
      $('#summernote').summernote('code',t.html);
      document.getElementById('tplName').value=t.name+' - Copia';
      document.getElementById('tplSubject').value='Oggetto per '+t.name;
    }
  });
}
function saveTemplate() {
  const name=document.getElementById('tplName').value;
  const subject=document.getElementById('tplSubject').value;
  const content=$('#summernote').summernote('code');
  if(!name||!subject||!content){alert('⚠️ Compila tutti i campi');return;}
  const formData=new FormData();
  formData.append('name',name); formData.append('subject',subject); formData.append('html_content',content);
  fetch('/api/template',{method:'POST',body:formData}).then(r=>r.text()).then(html=>{
    alert('✅ Template salvato!');
    window.location.href='/templates-list';
  }).catch(e=>alert('❌ Errore: '+e));
}
function previewTemplate() {
  document.getElementById('preview-content').innerHTML=$('#summernote').summernote('code');
  document.getElementById('preview').classList.remove('hidden');
}
</script></body></html>"""

@app.get("/templates", response_class=HTMLResponse)
def tpl_ui(req: Request=Depends(require_auth)): return HTMLResponse(TPL_HTML)
@app.get("/api/templates/premade")
def get_premade_templates(req: Request=Depends(require_auth)):
    return {t["id"]: t for t in PREMADE_TEMPLATES}
@app.get("/api/templates/json")
def get_tpls_json(req: Request=Depends(require_auth), db: Session=Depends(get_db)):
    return [{"id": t.id, "name": t.name, "subject": t.subject} for t in db.query(Template).all()]
@app.get("/api/template/{tid}")
def get_template_detail(tid: int, req: Request=Depends(require_auth), db: Session=Depends(get_db)):
    t = db.query(Template).filter(Template.id == tid).first()
    return {"id": t.id, "name": t.name, "subject": t.subject, "html_content": t.html_content} if t else {}
@app.post("/api/template")
async def create_tpl(req: Request=Depends(require_auth), db: Session=Depends(get_db), 
                    name: str=Form(...), subject: str=Form(...), html_content: str=Form(...), file: UploadFile=File(None)):
    try:
        attachment_path = ""
        if file and file.filename:
            os.makedirs("uploads", exist_ok=True)
            safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in file.filename)
            with open(os.path.join("uploads", safe_name), "wb") as buffer: buffer.write(await file.read())
            attachment_path = os.path.join("uploads", safe_name)
        db.add(Template(name=name, subject=subject, html_content=html_content, attachment_path=attachment_path))
        db.commit()
        return RedirectResponse(url="/templates-list", status_code=303)
    except Exception as e: 
        db.rollback()
        return HTMLResponse(f"<div class='p-4 text-center text-red-600'>❌ {e}</div>")
@app.delete("/api/template/{tid}")
def del_tpl(tid: int, req: Request=Depends(require_auth), db: Session=Depends(get_db)):
    t = db.query(Template).filter(Template.id==tid).first()
    if t: 
        if t.attachment_path and os.path.exists(t.attachment_path): os.remove(t.attachment_path)
        db.delete(t); db.commit()
    return RedirectResponse(url="/templates-list", status_code=303)

SCHEDULE_HTML = get_header("schedule") + """
<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Campagne</title>
<link href="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.10/index.global.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.10/index.global.min.js"></script>
<script src="https://unpkg.com/htmx.org@1.9.12"></script><script src="https://cdn.tailwindcss.com"></script>
</head><body class="bg-slate-50 min-h-screen">
<div class="max-w-7xl mx-auto px-6 py-8 space-y-6">
  <div class="bg-white rounded-xl shadow border border-slate-200 p-6">
    <h2 class="text-xl font-bold text-slate-800 mb-6">➕ Nuova Campagna</h2>
    <form hx-post="/api/campaign" hx-target="#calendar" hx-swap="none" hx-on::after-request="this.reset(); loadCalendar()" class="grid grid-cols-1 md:grid-cols-4 gap-4">
      <div><label class="block text-sm font-medium text-slate-700 mb-2">Nome Campagna *</label><input name="name" placeholder="Es: Maggio 2026" class="w-full px-4 py-2.5 border border-slate-300 rounded-lg" required></div>
      <div><label class="block text-sm font-medium text-slate-700 mb-2">Lista *</label><select name="list_id" id="list-select" class="w-full px-4 py-2.5 border border-slate-300 rounded-lg" required><option value="">Caricamento...</option></select></div>
      <div><label class="block text-sm font-medium text-slate-700 mb-2">Template *</label><select name="template_id" id="tpl-select" class="w-full px-4 py-2.5 border border-slate-300 rounded-lg" required><option value="">Caricamento...</option></select></div>
      <div><label class="block text-sm font-medium text-slate-700 mb-2">Data/Ora Invio *</label><input name="scheduled_at" type="datetime-local" class="w-full px-4 py-2.5 border border-slate-300 rounded-lg" required></div>
      <button type="submit" class="md:col-span-4 bg-slate-800 text-white py-2.5 rounded-lg font-semibold hover:bg-slate-900 transition">🚀 Crea Campagna</button>
    </form>
  </div>
  <div class="bg-amber-50 border-2 border-amber-200 rounded-xl p-6">
    <h3 class="font-bold text-amber-800 mb-2">🔧 Test Immediato</h3>
    <div class="flex gap-3"><select id="debug-campaign-select" class="flex-1 px-4 py-2 border border-amber-300 rounded-lg"><option value="">Caricamento...</option></select><button onclick="sendDebug()" class="bg-red-600 hover:bg-red-700 text-white px-6 py-2 rounded-lg font-semibold">🚀 Invia Ora</button></div>
    <div id="debug-result" class="mt-2 text-sm"></div>
  </div>
  <div class="bg-white rounded-xl shadow border border-slate-200 p-6"><div id="calendar"></div></div>
</div>
<script>
document.addEventListener('DOMContentLoaded', function() {
  fetch('/api/lists/json').then(r=>r.json()).then(l=>{
    const sel=document.getElementById('list-select');
    sel.innerHTML='<option value="">Seleziona Lista...</option>';
    l.forEach(x=>{const o=document.createElement('option');o.value=x.id;o.textContent=x.name;sel.appendChild(o);});
  });
  fetch('/api/templates/json').then(r=>r.json()).then(t=>{
    const sel=document.getElementById('tpl-select');
    sel.innerHTML='<option value="">Seleziona Template...</option>';
    t.forEach(x=>{const o=document.createElement('option');o.value=x.id;o.textContent=x.name;sel.appendChild(o);});
  });
  const params=new URLSearchParams(window.location.search);
  if(params.get('template_id')){
    document.getElementById('tpl-select').value=params.get('template_id');
  }
  fetch('/api/campaigns').then(r=>r.json()).then(c=>{
    const sel=document.getElementById('debug-campaign-select');
    sel.innerHTML='<option value="">Seleziona...</option>';
    c.forEach(x=>{const o=document.createElement('option');o.value=x.id;o.textContent=x.title;sel.appendChild(o);});
  });
  const el=document.getElementById('calendar');
  const calendar=new FullCalendar.Calendar(el,{
    initialView:'dayGridMonth',
    headerToolbar:{left:'prev,next today',center:'title',right:'dayGridMonth,timeGridWeek'},
    events:'/api/campaigns',
    locale:'it',
    firstDay:1,
    eventClick:function(info){
      if(info.event.id){ document.getElementById('debug-campaign-select').value=info.event.id; }
    }
  });
  calendar.render();
  window.loadCalendar = function() { calendar.refetchEvents(); };
});
function sendDebug(){
  const cid=document.getElementById('debug-campaign-select').value;
  const res=document.getElementById('debug-result');
  if(!cid){res.innerHTML='<span class="text-red-600">⚠️ Seleziona</span>';return;}
  res.innerHTML='<span class="text-amber-600">⏳ Invio...</span>';
  fetch('/api/debug/send-campaign/'+cid,{method:'POST'}).then(r=>r.json()).then(d=>{
    res.innerHTML=d.message?`<span class="text-emerald-600">${d.message}</span>`:`<span class="text-red-600">❌ ${d.detail||'Errore'}</span>`;
  });
}
</script></body></html>"""

@app.get("/schedule", response_class=HTMLResponse)
def schedule_ui(req: Request=Depends(require_auth)): return HTMLResponse(SCHEDULE_HTML)
@app.post("/api/campaign")
def create_campaign(req: Request=Depends(require_auth), db: Session=Depends(get_db), name: str=Form(...), list_id: int=Form(...), template_id: int=Form(...), scheduled_at: str=Form(...)):
    try:
        dt = datetime.fromisoformat(scheduled_at)
        c = Campaign(name=name, list_id=list_id, template_id=template_id, scheduled_at=dt, status="scheduled")
        db.add(c); db.commit(); db.refresh(c)
        return HTMLResponse("<div class='p-4 text-center text-emerald-600 font-semibold'>✅ Campagna creata!</div>")
    except Exception as e: db.rollback(); raise HTTPException(400, str(e))
@app.get("/api/campaigns")
def get_campaigns(req: Request=Depends(require_auth), db: Session=Depends(get_db)):
    camps = db.query(Campaign).all()
    return [{"title": f"{c.name} ({c.status})", "start": c.scheduled_at.isoformat(), "id": c.id} for c in camps]
@app.post("/api/debug/send-campaign/{campaign_id}")
def debug_send_campaign(campaign_id: int, req: Request = Depends(require_auth)):
    result = send_campaign_now(campaign_id)
    if "error" in result: raise HTTPException(status_code=400, detail=result["error"])
    return JSONResponse({"message": f"✅ Inviata: {result['sent']} email"})

# ✅ FIX: Cronologia con HTML corretto (niente Markdown)
CAMPAIGNS_HTML = get_header("campaigns") + """
<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cronologia</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-slate-50 min-h-screen">
<div class="max-w-7xl mx-auto px-6 py-8">
  <div class="bg-white rounded-xl shadow border border-slate-200 overflow-hidden">
    <table class="w-full text-sm">
      <thead class="bg-slate-50"><tr>
        <th class="p-4 text-left font-semibold text-slate-700">Campagna (Clicca)</th>
        <th class="p-4 text-left font-semibold text-slate-700">Lista</th>
        <th class="p-4 text-left font-semibold text-slate-700">Template</th>
        <th class="p-4 text-left font-semibold text-slate-700">Data</th>
        <th class="p-4 text-left font-semibold text-slate-700">Stato</th>
        <th class="p-4 text-left font-semibold text-slate-700">Inviate</th>
      </tr></thead>
      <tbody id="campaign-table"></tbody>
    </table>
  </div>
</div>
<script>
fetch('/api/campaigns/history').then(r=>r.json()).then(data=>{
  const tbody=document.getElementById('campaign-table');
  if(data.length===0){ tbody.innerHTML='<tr><td colspan="6" class="p-8 text-center text-slate-500">Nessuna campagna</td></tr>'; return; }
  tbody.innerHTML=data.map(c=>`<tr class="border-b hover:bg-slate-50 transition cursor-pointer" onclick="window.location.href='/campaign-detail?campaign_id=${c.id}'">
    <td class="p-4 font-medium text-blue-600 hover:underline">${c.name}</td>
    <td class="p-4 text-slate-600">${c.list_name}</td><td class="p-4 text-slate-600">${c.template_name}</td>
    <td class="p-4 text-slate-500">${c.scheduled_at}</td>
    <td class="p-4"><span class="px-2 py-1 rounded-full text-xs font-bold ${c.status==='completed'?'bg-emerald-100 text-emerald-800':'bg-amber-100 text-amber-800'}">${c.status}</span></td>
    <td class="p-4">${c.sent_count}</td></tr>`).join('');
});
</script></body></html>"""

@app.get("/campaigns", response_class=HTMLResponse)
def campaigns_history(req: Request=Depends(require_auth)): return HTMLResponse(CAMPAIGNS_HTML)
@app.get("/api/campaigns/history")
def get_campaigns_history(req: Request=Depends(require_auth), db: Session=Depends(get_db)):
    camps = db.query(Campaign).join(RecipientList, Campaign.list_id == RecipientList.id).join(Template, Campaign.template_id == Template.id).all()
    return [{"id": c.id, "name": c.name, "list_name": c.list.name, "template_name": c.template.name, 
             "scheduled_at": c.scheduled_at.strftime("%Y-%m-%d %H:%M"), "status": c.status, 
             "sent_count": db.query(CampaignLog).filter(CampaignLog.campaign_id == c.id).count()} for c in camps]

# ✅ FIX: Dettaglio Campagna con HTML corretto e script robusto
CAMPAIGN_DETAIL_HTML = get_header() + """
<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dettaglio Campagna</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-slate-50 min-h-screen">
<div class="max-w-7xl mx-auto px-6 py-8">
  <div class="mb-4"><a href="/campaigns" class="text-slate-600 hover:text-slate-900">← Torna alla Cronologia</a></div>
  <div class="bg-white rounded-xl shadow border border-slate-200 p-6 mb-6">
    <h2 class="text-2xl font-bold text-slate-800" id="camp-title">Caricamento...</h2>
    <p class="text-slate-500 mt-1">Dettagli invio e tracking</p>
  </div>
  <div class="bg-white rounded-xl shadow border border-slate-200 overflow-hidden">
    <table class="w-full text-sm">
      <thead class="bg-slate-50"><tr>
        <th class="p-4 text-left font-semibold text-slate-700">Nome</th>
        <th class="p-4 text-left font-semibold text-slate-700">Cognome</th>
        <th class="p-4 text-left font-semibold text-slate-700">Email</th>
        <th class="p-4 text-left font-semibold text-slate-700">Ora Invio</th>
        <th class="p-4 text-left font-semibold text-slate-700">Stato Consegna</th>
        <th class="p-4 text-left font-semibold text-slate-700">Prima Apertura</th>
      </tr></thead>
      <tbody id="detail-table"></tbody>
    </table>
  </div>
</div>
<script>
const params=new URLSearchParams(window.location.search);
const cid=params.get('campaign_id');
if(!cid){ window.location.href='/campaigns'; }

fetch(`/api/campaign/${cid}/details`).then(r=>r.json()).then(data=>{
  if(data.error) {
    document.getElementById('camp-title').textContent = data.error;
    return;
  }
  
  document.getElementById('camp-title').textContent = data.campaign_name + ' (' + data.status + ')';
  const tbody=document.getElementById('detail-table');
  
  if(!data.recipients || data.recipients.length === 0){ 
    tbody.innerHTML='<tr><td colspan="6" class="p-8 text-center text-slate-500">Nessun destinatario trovato per questa campagna.</td></tr>'; 
    return; 
  }
  
  tbody.innerHTML=data.recipients.map(r=>`<tr class="border-b hover:bg-slate-50">
    <td class="p-4">${r.nome}</td><td class="p-4">${r.cognome}</td><td class="p-4 font-medium">${r.email}</td>
    <td class="p-4 text-slate-500">${r.sent_at}</td>
    <td class="p-4"><span class="px-2 py-1 rounded text-xs font-bold ${r.status==='sent'?'bg-emerald-100 text-emerald-800':'bg-red-100 text-red-800'}">${r.status}</span></td>
    <td class="p-4 text-slate-500">${r.opened_at || '-'}</td>
  </tr>`).join('');
}).catch(e=>console.error('Errore caricamento dettagli:', e));
</script></body></html>"""

@app.get("/campaign-detail", response_class=HTMLResponse)
def campaign_detail_ui(req: Request=Depends(require_auth)): return HTMLResponse(CAMPAIGN_DETAIL_HTML)

@app.get("/api/campaign/{cid}/details")
def get_campaign_details(cid: int, req: Request=Depends(require_auth), db: Session=Depends(get_db)):
    camp = db.query(Campaign).filter(Campaign.id == cid).first()
    if not camp: return {"error": "Campagna non trovata"}
    
    # Debug log per verificare i dati
    total_logs = db.query(CampaignLog).filter(CampaignLog.campaign_id == cid).count()
    logs = db.query(CampaignLog).join(Recipient, CampaignLog.recipient_id == Recipient.id).filter(CampaignLog.campaign_id == cid).all()
    
    logging.info(f"Dettaglio Campagna {cid}: Trovati {total_logs} log totali, {len(logs)} log con destinatario.")
    
    recipients = []
    for log in logs:
        first_open = db.query(EmailEvent).filter(
            EmailEvent.email == log.recipient.email, 
            EmailEvent.event_type == 'open'
        ).order_by(EmailEvent.timestamp.asc()).first()
        
        recipients.append({
            "nome": log.recipient.nome,
            "cognome": log.recipient.cognome,
            "email": log.recipient.email,
            "sent_at": log.sent_at.strftime("%Y-%m-%d %H:%M:%S"),
            "status": log.status,
            "opened_at": first_open.timestamp.strftime("%Y-%m-%d %H:%M") if first_open else None
        })
        
    return {"campaign_name": camp.name, "status": camp.status, "recipients": recipients}