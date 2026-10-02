"""
Retail Agents — Pius General Dealer
Flask backend with Gemini Free API + dual key rotation.
Database: PostgreSQL on Render (persistent) / SQLite locally.
Run locally: python app.py  →  http://localhost:5000
"""

import os, json, csv, io, datetime
from flask import Flask, request, jsonify, send_from_directory, Response
from dotenv import load_dotenv
from functools import wraps

# ── Load environment variables ──────────────────────────────────────────────
load_dotenv()
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY",  "")
GEMINI_API_KEY_2 = os.getenv("GEMINI_API_KEY_2", "")
STORE_PASSWORD   = os.getenv("STORE_PASSWORD", "pius@1")
DATABASE_URL     = os.getenv("DATABASE_URL", "")   # set by Render automatically

# ── Database mode: PostgreSQL (Render) or SQLite (local) ────────────────────
USE_POSTGRES = bool(DATABASE_URL and DATABASE_URL.startswith("postgres"))

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    # Render gives "postgres://" but psycopg2 wants "postgresql://"
    DB_DSN = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    print("✅ Using PostgreSQL (Render)")
else:
    import sqlite3
    DB_PATH = os.path.join("data", "retail.db")
    print("✅ Using SQLite (local dev)")

# ── Gemini key pool ──────────────────────────────────────────────────────────
_key_pool       = [k for k in [GEMINI_API_KEY, GEMINI_API_KEY_2] if k]
_active_key_idx = 0

def _get_active_key():
    return _key_pool[_active_key_idx % len(_key_pool)] if _key_pool else ""

def _rotate_key():
    global _active_key_idx
    _active_key_idx = (_active_key_idx + 1) % max(1, len(_key_pool))
    if _key_pool:
        try:
            import google.generativeai as genai
            genai.configure(api_key=_get_active_key())
            print(f"🔄 Gemini key rotated → key #{_active_key_idx + 1}")
        except Exception:
            pass

# ── Password guard decorator ─────────────────────────────────────────────────
def require_password(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        pw = request.headers.get("X-Store-Password","") or \
             (request.get_json(silent=True) or {}).get("password","")
        if pw != STORE_PASSWORD:
            return jsonify({"error": "Incorrect password", "auth": False}), 401
        return f(*args, **kwargs)
    return decorated

# ── Gemini setup ─────────────────────────────────────────────────────────────
GEMINI_AVAILABLE = False
gemini_model     = None
try:
    import google.generativeai as genai
    if _key_pool:
        genai.configure(api_key=_get_active_key())
        gemini_model     = genai.GenerativeModel("gemini-1.5-flash")
        GEMINI_AVAILABLE = True
        print(f"✅ Gemini 1.5 Flash — {len(_key_pool)} key(s) loaded")
    else:
        print("⚠  No GEMINI_API_KEY set — rule-based fallback active")
except ImportError:
    print("⚠  google-generativeai not installed")

# ── Flask app ────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder="static", template_folder="templates")

# ════════════════════════════════════════════════════════════════════════════
# DATABASE ABSTRACTION — works with both Postgres and SQLite
# ════════════════════════════════════════════════════════════════════════════

def get_db():
    """Return a connection. Caller must close it."""
    if USE_POSTGRES:
        conn = psycopg2.connect(DB_DSN, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = False
        return conn
    else:
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(DB_PATH)
        conn.row_factory = _sqlite3.Row
        return conn

def ph(n=1):
    """Return n placeholders: %s for Postgres, ? for SQLite."""
    p = "%s" if USE_POSTGRES else "?"
    return ", ".join([p] * n)

def P():
    """Single placeholder."""
    return "%s" if USE_POSTGRES else "?"

def serial():
    """Auto-increment syntax."""
    return "SERIAL" if USE_POSTGRES else "INTEGER"

def now_fn():
    return "NOW()" if USE_POSTGRES else "datetime('now')"

def local_now():
    return "NOW()" if USE_POSTGRES else "datetime('now','localtime')"

def rows_to_dicts(rows):
    if USE_POSTGRES:
        return [dict(r) for r in rows]
    return [dict(r) for r in rows]

def init_db():
    if not USE_POSTGRES:
        os.makedirs("data", exist_ok=True)

    conn = get_db()
    c    = conn.cursor()

    if USE_POSTGRES:
        c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            sku         TEXT PRIMARY KEY,
            stock       REAL DEFAULT 0,
            lead_days   INTEGER DEFAULT 2,
            cost        REAL DEFAULT 0,
            price       REAL DEFAULT 0,
            comp_price  REAL DEFAULT 0,
            sensitivity TEXT DEFAULT 'Medium',
            hist        TEXT DEFAULT '[]'
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id          SERIAL PRIMARY KEY,
            name        TEXT UNIQUE,
            price_rtg   TEXT DEFAULT 'Fair',
            lead_time   TEXT DEFAULT '3 days',
            on_time     REAL DEFAULT 80,
            variability TEXT DEFAULT 'Medium'
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id          SERIAL PRIMARY KEY,
            name        TEXT UNIQUE,
            segment     TEXT DEFAULT 'Regular',
            days_since  INTEGER DEFAULT 0,
            avg_basket  REAL DEFAULT 0
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id          SERIAL PRIMARY KEY,
            agent       TEXT,
            title       TEXT,
            description TEXT,
            risk        TEXT DEFAULT 'Medium',
            status      TEXT DEFAULT 'Pending',
            cost        REAL DEFAULT 0,
            value       REAL DEFAULT 0,
            mod_note    TEXT DEFAULT '',
            created_at  TIMESTAMP DEFAULT NOW()
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS sales_log (
            id          SERIAL PRIMARY KEY,
            sku         TEXT,
            qty         REAL,
            sell_price  REAL,
            revenue     REAL,
            profit      REAL,
            customer    TEXT DEFAULT 'Walk-in',
            sold_at     TIMESTAMP DEFAULT NOW()
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id          SERIAL PRIMARY KEY,
            type        TEXT,
            title       TEXT,
            message     TEXT,
            priority    TEXT DEFAULT 'Medium',
            read        INTEGER DEFAULT 0,
            created_at  TIMESTAMP DEFAULT NOW()
        )""")
    else:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            sku TEXT PRIMARY KEY, stock REAL DEFAULT 0, lead_days INTEGER DEFAULT 2,
            cost REAL DEFAULT 0, price REAL DEFAULT 0, comp_price REAL DEFAULT 0,
            sensitivity TEXT DEFAULT 'Medium', hist TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,
            price_rtg TEXT DEFAULT 'Fair', lead_time TEXT DEFAULT '3 days',
            on_time REAL DEFAULT 80, variability TEXT DEFAULT 'Medium'
        );
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,
            segment TEXT DEFAULT 'Regular', days_since INTEGER DEFAULT 0, avg_basket REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, title TEXT,
            description TEXT, risk TEXT DEFAULT 'Medium', status TEXT DEFAULT 'Pending',
            cost REAL DEFAULT 0, value REAL DEFAULT 0, mod_note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sales_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT, qty REAL,
            sell_price REAL, revenue REAL, profit REAL, customer TEXT DEFAULT 'Walk-in',
            sold_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, title TEXT,
            message TEXT, priority TEXT DEFAULT 'Medium', read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """)

    # Check if products table is empty
    c.execute("SELECT COUNT(*) FROM products")
    row = c.fetchone()
    count = row[0] if not USE_POSTGRES else row['count']

    if count == 0:
        sample_products = [
            ("Maize Meal 5kg",   120, 2, 38.50, 52.00, 50.00, "Medium", json.dumps([95,102,110,108,115,112,118,120])),
            ("White Bread",       85, 1, 12.00, 18.50, 17.99, "High",   json.dumps([200,210,195,220,215,225,218,230])),
            ("Fresh Milk 2L",     60, 1, 22.00, 32.00, 31.50, "High",   json.dumps([150,148,155,160,158,162,165,168])),
            ("Sunflower Oil 2L",  60, 3, 45.00, 68.00, 65.00, "Low",    json.dumps([40,42,45,44,48,47,50,52])),
            ("Rice 2kg",          95, 4, 28.00, 42.00, 40.00, "Medium", json.dumps([70,75,72,80,78,82,85,88])),
            ("Sugar 2kg",         80, 2, 24.00, 36.00, 35.00, "Medium", json.dumps([60,65,68,70,72,75,78,80])),
            ("Eggs (6 pack)",     70, 1, 18.00, 28.00, 27.50, "High",   json.dumps([120,125,118,130,128,135,132,138])),
            ("Canned Beans 420g", 55, 3,  8.50, 14.00, 13.50, "Medium", json.dumps([45,48,50,52,49,55,53,57])),
        ]
        for row in sample_products:
            c.execute(f"INSERT INTO products VALUES ({ph(8)})", row)

        for row in [
            ("Premier Foods","Good","2 days",92,"Low"),
            ("Tiger Brands","Good","3 days",78,"Medium"),
            ("Agri-Supply SA","Fair","1 day",94,"Low"),
            ("Local Wholesaler","Poor","1 day",65,"High"),
        ]:
            c.execute(f"INSERT INTO suppliers (name,price_rtg,lead_time,on_time,variability) VALUES ({ph(5)})", row)

        for row in [
            ("Thandi Mokoena","Loyal",5,320),
            ("Johan van Wyk","Regular",14,180),
            ("Lerato Dlamini","At-Risk",28,95),
            ("Sipho Nkosi","At-Risk",35,140),
            ("Nomsa Khumalo","Loyal",3,480),
        ]:
            c.execute(f"INSERT INTO customers (name,segment,days_since,avg_basket) VALUES ({ph(4)})", row)

    conn.commit()
    conn.close()
    print("✅ Database ready")

# ── Helpers ──────────────────────────────────────────────────────────────────
def load_store_data():
    conn = get_db()
    c    = conn.cursor()
    c.execute("SELECT * FROM products")
    products  = rows_to_dicts(c.fetchall())
    c.execute("SELECT * FROM suppliers")
    suppliers = rows_to_dicts(c.fetchall())
    c.execute("SELECT * FROM customers")
    customers = rows_to_dicts(c.fetchall())
    conn.close()
    for p in products:
        try:    p["hist"] = json.loads(p["hist"])
        except: p["hist"] = []
    return products, suppliers, customers

def compute_rop(product, factor=1.4):
    hist  = product.get("hist", [])
    avg   = sum(hist[-4:])/4 if len(hist)>=4 else (sum(hist)/len(hist) if hist else 0)
    return max(1, round((avg/7) * product.get("lead_days",2) * factor))

def forecast(product):
    hist   = product.get("hist",[0,0,0,0])
    avg    = sum(hist[-4:])/min(4,len(hist)) if hist else 0
    trend  = (hist[-1]-hist[0])/max(1,len(hist)-1) if len(hist)>1 else 0
    d      = datetime.date.today().day
    fc     = avg + trend*2
    if (d>=25 or d<=3) and product.get("sensitivity")=="High": fc*=1.15
    return round(max(0,fc))

# ════════════════════════════════════════════════════════════════════════════
# ROUTES
# ════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return send_from_directory(".", "retail-agent.html")

@app.route("/api/auth", methods=["POST"])
def api_auth():
    data = request.get_json(silent=True) or {}
    if data.get("password") == STORE_PASSWORD:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Incorrect password"}), 401

@app.route("/api/data")
def api_data():
    products, suppliers, customers = load_store_data()
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM decisions ORDER BY created_at DESC")
    decisions = rows_to_dicts(c.fetchall())
    conn.close()
    return jsonify({"products":products,"suppliers":suppliers,"customers":customers,
                    "decisions":decisions,"gemini":GEMINI_AVAILABLE,"key_count":len(_key_pool)})

@app.route("/api/stats")
def api_stats():
    conn = get_db(); c = conn.cursor()
    today    = datetime.date.today().isoformat()
    week_ago = (datetime.date.today()-datetime.timedelta(days=7)).isoformat()
    p = P()

    def q1(sql, *args):
        c.execute(sql, args); r = c.fetchone()
        return list(r.values())[0] if USE_POSTGRES else r[0]

    stats = {
        "pending":      q1(f"SELECT COUNT(*) FROM decisions WHERE status={p}", "Pending"),
        "approved":     q1(f"SELECT COUNT(*) FROM decisions WHERE status IN ('Approved','Auto-approved')"),
        "sales_today":  q1(f"SELECT COALESCE(SUM(revenue),0) FROM sales_log WHERE DATE(sold_at)={p}", today),
        "units_today":  q1(f"SELECT COALESCE(SUM(qty),0)     FROM sales_log WHERE DATE(sold_at)={p}", today),
        "profit_today": q1(f"SELECT COALESCE(SUM(profit),0)  FROM sales_log WHERE DATE(sold_at)={p}", today),
        "sales_week":   q1(f"SELECT COALESCE(SUM(revenue),0) FROM sales_log WHERE DATE(sold_at)>={p}", week_ago),
        "profit_week":  q1(f"SELECT COALESCE(SUM(profit),0)  FROM sales_log WHERE DATE(sold_at)>={p}", week_ago),
        "unread_alerts":q1("SELECT COUNT(*) FROM alerts WHERE read=0"),
    }
    c.execute(f"SELECT sku, SUM(qty) as total FROM sales_log WHERE DATE(sold_at)>={p} GROUP BY sku ORDER BY total DESC LIMIT 1", (week_ago,))
    tp = c.fetchone()
    stats["top_product"] = {"sku": tp["sku"] if USE_POSTGRES else tp[0], "qty": tp["total"] if USE_POSTGRES else tp[1]} if tp else None
    conn.close()
    return jsonify(stats)

@app.route("/api/alerts")
def api_alerts():
    products, suppliers, customers = load_store_data()
    conn = get_db(); c = conn.cursor()
    p = P()

    for prod in products:
        rop = compute_rop(prod)
        if prod["stock"] <= rop:
            priority = "High" if prod["stock"] <= rop*0.5 else "Medium"
            title    = f"Low Stock: {prod['sku']}"
            c.execute(f"SELECT 1 FROM alerts WHERE title={p} AND read=0", (title,))
            if not c.fetchone():
                c.execute(f"INSERT INTO alerts (type,title,message,priority) VALUES ({ph(4)})",
                    ("stock", title, f"{prod['sku']} has only {prod['stock']} units (reorder point: {rop}).", priority))

    at_risk = [cu for cu in customers if cu["segment"]=="At-Risk" or cu["days_since"]>21]
    if at_risk:
        t = "At-Risk Customers"
        c.execute(f"SELECT 1 FROM alerts WHERE title={p} AND read=0", (t,))
        if not c.fetchone():
            c.execute(f"INSERT INTO alerts (type,title,message,priority) VALUES ({ph(4)})",
                ("customer",t,f"{len(at_risk)} customers haven't visited in 21+ days: {', '.join(x['name'] for x in at_risk[:3])}.","Medium"))

    bad = [s for s in suppliers if s["on_time"]<75]
    if bad:
        t = "Unreliable Suppliers"
        c.execute(f"SELECT 1 FROM alerts WHERE title={p} AND read=0", (t,))
        if not c.fetchone():
            c.execute(f"INSERT INTO alerts (type,title,message,priority) VALUES ({ph(4)})",
                ("supplier",t,f"{', '.join(s['name'] for s in bad)} — on-time below 75%.","Low"))

    conn.commit()
    c.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT 50")
    alerts = rows_to_dicts(c.fetchall())
    conn.close()
    return jsonify(alerts)

@app.route("/api/alerts/read/<int:alert_id>", methods=["POST"])
def api_alert_read(alert_id):
    conn = get_db(); c = conn.cursor()
    c.execute(f"UPDATE alerts SET read=1 WHERE id={P()}", (alert_id,))
    conn.commit(); conn.close()
    return jsonify({"ok":True})

@app.route("/api/alerts/read-all", methods=["POST"])
def api_alerts_read_all():
    conn = get_db(); c = conn.cursor()
    c.execute("UPDATE alerts SET read=1")
    conn.commit(); conn.close()
    return jsonify({"ok":True})

@app.route("/api/trends")
def api_trends():
    conn = get_db(); c = conn.cursor()
    days     = int(request.args.get("days",7))
    start    = (datetime.date.today()-datetime.timedelta(days=days)).isoformat()
    p = P()
    c.execute(f"SELECT DATE(sold_at) as day, SUM(revenue) as rev, SUM(profit) as prof, SUM(qty) as units "
              f"FROM sales_log WHERE DATE(sold_at)>={p} GROUP BY DATE(sold_at) ORDER BY DATE(sold_at)", (start,))
    daily = rows_to_dicts(c.fetchall())
    c.execute(f"SELECT sku, SUM(qty) as total, SUM(revenue) as rev FROM sales_log "
              f"WHERE DATE(sold_at)>={p} GROUP BY sku ORDER BY total DESC LIMIT 5", (start,))
    top = rows_to_dicts(c.fetchall())
    conn.close()
    return jsonify({"daily":daily,"top_products":top})

@app.route("/api/margins")
def api_margins():
    products, _, _ = load_store_data()
    margins = []
    for p in products:
        if p["price"]>0:
            gp  = p["price"]-p["cost"]
            pct = gp/p["price"]*100
            vs  = ((p["price"]-p["comp_price"])/p["comp_price"]*100) if p["comp_price"]>0 else 0
            margins.append({"sku":p["sku"],"cost":p["cost"],"price":p["price"],
                "comp_price":p["comp_price"],"gp":round(gp,2),"margin_pct":round(pct,1),
                "vs_comp":round(vs,1),"stock_value":round(p["stock"]*p["cost"],2)})
    avg = sum(m["margin_pct"] for m in margins)/max(1,len(margins))
    return jsonify({"margins":margins,"avg_margin":round(avg,1)})

@app.route("/api/import/<kind>", methods=["POST"])
@require_password
def api_import(kind):
    if kind not in ("products","suppliers","customers"):
        return jsonify({"error":"Unknown data type"}),400
    file = request.files.get("file")
    if not file: return jsonify({"error":"No file uploaded"}),400
    text   = file.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows   = list(reader)
    if not rows: return jsonify({"error":"CSV is empty"}),400
    ALIASES = {"competitor_price":"comp_price","comp":"comp_price","price_rating":"price_rtg",
               "on_time_pct":"on_time","days_since_visit":"days_since","basket":"avg_basket"}
    norm = []
    for row in rows:
        nr = {}
        for k,v in row.items():
            key = k.strip().lower().replace(" ","_")
            nr[ALIASES.get(key,key)] = v.strip()
        norm.append(nr)
    conn = get_db(); c = conn.cursor(); count = 0
    try:
        p = P()
        if kind=="products":
            c.execute("DELETE FROM products")
            for r in norm:
                sku = r.get("sku") or r.get("name","")
                if not sku: continue
                hist = [float(r.get(f"sales_w{i}",0) or 0) for i in range(1,9)]
                c.execute(f"INSERT INTO products VALUES ({ph(8)})",
                    (sku,float(r.get("stock",0) or 0),int(r.get("lead_days",2) or 2),
                     float(r.get("cost",0) or 0),float(r.get("price",0) or 0),
                     float(r.get("comp_price",0) or 0),r.get("sensitivity","Medium"),json.dumps(hist)))
                count+=1
        elif kind=="suppliers":
            c.execute("DELETE FROM suppliers")
            for r in norm:
                name = r.get("name","")
                if not name: continue
                c.execute(f"INSERT INTO suppliers (name,price_rtg,lead_time,on_time,variability) VALUES ({ph(5)})",
                    (name,r.get("price_rtg","Fair"),r.get("lead_time","3 days"),
                     float(r.get("on_time",80) or 80),r.get("variability","Medium")))
                count+=1
        elif kind=="customers":
            c.execute("DELETE FROM customers")
            for r in norm:
                name = r.get("name","")
                if not name: continue
                c.execute(f"INSERT INTO customers (name,segment,days_since,avg_basket) VALUES ({ph(4)})",
                    (name,r.get("segment","Regular"),int(r.get("days_since",0) or 0),
                     float(r.get("avg_basket",0) or 0)))
                count+=1
        conn.commit()
    except Exception as e:
        conn.rollback(); return jsonify({"error":str(e)}),500
    finally:
        conn.close()
    return jsonify({"success":True,"count":count,"kind":kind})

@app.route("/api/template/<kind>")
def api_template(kind):
    templates = {
        "products":  "sku,stock,lead_days,cost,price,comp_price,sensitivity,sales_w1,sales_w2,sales_w3,sales_w4,sales_w5,sales_w6,sales_w7,sales_w8\nMaize Meal 5kg,120,2,38.50,52.00,50.00,Medium,95,102,110,108,115,112,118,120\nWhite Bread,85,1,12.00,18.50,17.99,High,200,210,195,220,215,225,218,230",
        "suppliers": "name,price_rtg,lead_time,on_time,variability\nPremier Foods,Good,2 days,92,Low\nTiger Brands,Good,3 days,78,Medium",
        "customers": "name,segment,days_since,avg_basket\nThandi Mokoena,Loyal,5,320\nLerato Dlamini,At-Risk,28,95",
    }
    if kind not in templates: return "Not found",404
    return Response(templates[kind],mimetype="text/csv",
        headers={"Content-Disposition":f"attachment; filename={kind}-template.csv"})

@app.route("/api/decision", methods=["POST"])
def api_decision():
    data = request.get_json(); p = P()
    conn = get_db(); c = conn.cursor()
    if data.get("id"):
        c.execute(f"UPDATE decisions SET status={p}, mod_note={p} WHERE id={p}",
            (data["status"],data.get("mod_note",""),data["id"]))
    else:
        c.execute(f"INSERT INTO decisions (agent,title,description,risk,status,cost,value) VALUES ({ph(7)})",
            (data.get("agent",""),data.get("title",""),data.get("description",""),
             data.get("risk","Medium"),data.get("status","Pending"),data.get("cost",0),data.get("value",0)))
    conn.commit(); conn.close()
    return jsonify({"success":True})

@app.route("/api/sale", methods=["POST"])
def api_sale():
    data = request.get_json(); p = P()
    sku  = data.get("sku",""); qty = float(data.get("qty",1))
    sell = float(data.get("sell_price",0)); customer = data.get("customer","Walk-in")
    conn = get_db(); c = conn.cursor()
    c.execute(f"SELECT * FROM products WHERE sku={p}", (sku,))
    product = c.fetchone()
    if not product: conn.close(); return jsonify({"error":"Product not found"}),404
    stock = product["stock"] if USE_POSTGRES else product[1]
    if stock < qty: conn.close(); return jsonify({"error":f"Only {stock} units in stock"}),400
    price   = sell or (product["price"] if USE_POSTGRES else product[4])
    cost    = product["cost"]  if USE_POSTGRES else product[3]
    revenue = price*qty; profit = (price-cost)*qty
    c.execute(f"UPDATE products SET stock=stock-{p} WHERE sku={p}", (qty,sku))
    c.execute(f"INSERT INTO sales_log (sku,qty,sell_price,revenue,profit,customer) VALUES ({ph(6)})",
        (sku,qty,price,revenue,profit,customer))
    conn.commit()
    c.execute(f"SELECT stock FROM products WHERE sku={p}", (sku,))
    new_stock = c.fetchone()["stock"] if USE_POSTGRES else c.fetchone()[0]
    conn.close()
    return jsonify({"success":True,"revenue":revenue,"profit":profit,"new_stock":new_stock})

@app.route("/api/sales-log")
def api_sales_log():
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM sales_log ORDER BY sold_at DESC LIMIT 200")
    rows = rows_to_dicts(c.fetchall()); conn.close()
    return jsonify(rows)

@app.route("/api/clear/<kind>", methods=["POST"])
@require_password
def api_clear(kind):
    tables = {"products":"products","suppliers":"suppliers","customers":"customers",
              "saleslog":"sales_log","decisions":"decisions","alerts":"alerts"}
    conn = get_db(); c = conn.cursor()
    if kind=="all":
        for t in tables.values(): c.execute(f"DELETE FROM {t}")
    elif kind in tables:
        c.execute(f"DELETE FROM {tables[kind]}")
    else:
        conn.close(); return jsonify({"error":"Unknown section"}),400
    conn.commit(); conn.close()
    return jsonify({"success":True,"cleared":kind})

# ── Gemini Chat ───────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a smart retail assistant AI for Pius General Dealer, Limpopo, South Africa.
Help the owner make decisions about stock, pricing, suppliers, customers.
Give practical, concise answers (2-3 sentences). Always recommend the owner reviews before acting.
Warm, professional tone for a South African retail owner.

STORE DATA:
{store_data}
"""

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(); user_msg = data.get("message","").strip()
    if not user_msg: return jsonify({"reply":"Please ask me something about your store."}),400

    products, suppliers, customers = load_store_data()
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT title,status,risk FROM decisions ORDER BY created_at DESC LIMIT 10")
    decisions = rows_to_dicts(c.fetchall()); conn.close()

    low_stock = [p for p in products if p["stock"]<=compute_rop(p)]
    at_risk   = [cu for cu in customers if cu["segment"]=="At-Risk" or cu["days_since"]>21]
    d         = datetime.date.today()

    store_summary = f"""
Products: {len(products)}, low stock: {[p['sku'] for p in low_stock]}.
Top forecast: {[p['sku'] for p in sorted(products,key=forecast,reverse=True)[:3]]}.
Suppliers: {len(suppliers)}, unreliable: {[s['name'] for s in suppliers if s['on_time']<75]}.
Customers: {len(customers)}, at-risk: {[c['name'] for c in at_risk]}.
Decisions: {[f"{d['title']} ({d['status']})" for d in decisions[:5]]}.
Date: {d}, payday: {d.day>=25 or d.day<=3}.
"""

    if GEMINI_AVAILABLE:
        import google.generativeai as genai
        prompt = SYSTEM_PROMPT.format(store_data=store_summary) + f"\n\nOwner asks: {user_msg}"
        for _attempt in range(len(_key_pool)):
            try:
                resp = gemini_model.generate_content(prompt)
                return jsonify({"reply":resp.text.strip(),"gemini":True,"key_used":_active_key_idx+1})
            except Exception as e:
                err = str(e)
                if any(w in err.lower() for w in ["quota","rate","429"]):
                    _rotate_key(); genai.configure(api_key=_get_active_key())
                else:
                    break
        return jsonify({"reply": rule_based_reply(user_msg,products,suppliers,customers,low_stock,at_risk), "gemini":False})
    return jsonify({"reply": rule_based_reply(user_msg,products,suppliers,customers,low_stock,at_risk), "gemini":False})

def rule_based_reply(msg, products, suppliers, customers, low_stock, at_risk):
    q = msg.lower()
    if any(w in q for w in ["stock","reorder","low"]):
        if low_stock: return f"⚠ {len(low_stock)} items need reordering: {', '.join(p['sku'] for p in low_stock[:3])}."
        return "✅ All products are above their reorder points."
    if any(w in q for w in ["demand","forecast","sell","top"]):
        top = sorted(products,key=forecast,reverse=True)[:3]
        return "📈 Top demand: "+", ".join(f"{p['sku']} ({forecast(p)} units)" for p in top)+"."
    if "supplier" in q:
        bad = [s for s in suppliers if s["on_time"]<75]
        return f"🚚 {len(suppliers)} suppliers. Unreliable: {', '.join(s['name'] for s in bad) or 'none'}."
    if any(w in q for w in ["customer","churn","risk","loyal"]):
        return f"🛍 {len(at_risk)} at-risk customers: {', '.join(c['name'] for c in at_risk[:3]) or 'none'}."
    if any(w in q for w in ["price","margin","profit"]):
        avg = sum((p["price"]-p["cost"])/p["price"]*100 for p in products if p["price"]>0)/max(1,len(products))
        return f"💰 Average gross margin: {avg:.1f}%."
    if any(w in q for w in ["hello","hi","hey","help"]):
        return f"👋 Hello! Ask me about stock, demand, suppliers or customers. You have {len(products)} products."
    return f"📊 {len(products)} products, {len(low_stock)} need reorder, {len(at_risk)} at-risk customers."

@app.route("/api/signals")
def api_signals():
    d = datetime.date.today(); payday = d.day>=25 or d.day<=3
    return jsonify({"payday":payday,"date":d.isoformat(),"weekday":d.strftime("%A"),
                    "weather":"Sunny, 28°C","notes":"Payday window — +15% uplift on staples" if payday else "Normal demand"})

@app.route("/api/gemini-status")
def api_gemini_status():
    return jsonify({"available":GEMINI_AVAILABLE,"key_count":len(_key_pool),"active_key":_active_key_idx+1,
                    "db_mode":"postgresql" if USE_POSTGRES else "sqlite"})

# ── Start ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    port  = int(os.getenv("PORT",5000))
    debug = os.getenv("FLASK_DEBUG","true").lower()=="true"
    print(f"\n🏪 Retail Agents — Pius General Dealer")
    print(f"   http://localhost:{port}")
    print(f"   DB:     {'PostgreSQL' if USE_POSTGRES else 'SQLite (local)'}")
    print(f"   Gemini: {'✅ '+str(len(_key_pool))+' key(s)' if GEMINI_AVAILABLE else '⚠  not configured'}\n")
    app.run(debug=debug,port=port,host="0.0.0.0")
else:
    # Called by gunicorn — init DB on startup
    init_db()
