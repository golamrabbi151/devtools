"""Mixed-crowd load test for DevTools Web (needs: pip install requests psutil openpyxl).
Run the app with APP_ENV=testing (rate limiting off) so you measure capacity, not 429s:
    APP_ENV=testing BIND=127.0.0.1:8100 gunicorn -c gunicorn.conf.py wsgi:app --daemon --pid app.pid
    python scripts/loadtest.py app.pid 150 30     # pidfile, concurrent clients, seconds
Only test servers you own."""
import io, os, random, re, statistics, sys, threading, time, json, collections
import requests, psutil
from openpyxl import Workbook
BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8100")
pidfile, CONC, SECS = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])

wb = Workbook(); ws = wb.active; ws.append(["id", "name", "price"])
for i in range(1000): ws.append([i, f"item {i}", i * 1.5])
b = io.BytesIO(); wb.save(b); XLSX = b.getvalue()
BIG = json.dumps({"rows": [{"id": i, "n": f"n{i}"} for i in range(2000)]})

def light(s, tok):  return s.post(f"{BASE}/tool/json-format/run", data={"csrf_token": tok("json-format"), "input": '{"a":[1,2]}', "action": "format"}, timeout=60)
def page(s, tok):   return s.get(f"{BASE}/tool/cron", timeout=60)
def cron(s, tok):   return s.post(f"{BASE}/tool/cron/run", data={"csrf_token": tok("cron"), "input": "*/5 * * * *", "tz": "UTC", "count": "10", "action": "parse"}, timeout=60)
def medium(s, tok): return s.post(f"{BASE}/tool/json-format/run", data={"csrf_token": tok("json-format"), "input": BIG, "action": "format"}, timeout=60)
def heavy(s, tok):  return s.post(f"{BASE}/tool/excel-json/run", data={"csrf_token": tok("excel-json"), "header": "on", "indent": "2"}, files={"file": ("a.xlsx", XLSX)}, timeout=60)
MIX = [(light, 45), (page, 25), (cron, 15), (medium, 10), (heavy, 5)]
FUNCS = [f for f, w in MIX for _ in range(w)]

lat, codes, errs, stop = collections.defaultdict(list), collections.Counter(), collections.Counter(), threading.Event()
def client():
    s = requests.Session(); cache = {}
    def tok(slug):
        if slug not in cache: cache[slug] = re.search(r'name="csrf_token" value="([^"]+)"', s.get(f"{BASE}/tool/{slug}", timeout=60).text).group(1)
        return cache[slug]
    while not stop.is_set():
        f = random.choice(FUNCS); t0 = time.perf_counter()
        try:
            r = f(s, tok); codes[r.status_code] += 1; lat[f.__name__].append((time.perf_counter() - t0) * 1000)
        except Exception as e:
            errs[type(e).__name__] += 1
        time.sleep(random.uniform(0.05, 0.3))          # "think time"

master = psutil.Process(int(open(pidfile).read()))
threads = [threading.Thread(target=client, daemon=True) for _ in range(CONC)]
t0 = time.time(); [t.start() for t in threads]; peak = 0
while time.time() - t0 < SECS:
    time.sleep(1)
    try: peak = max(peak, sum(c.memory_info().rss for c in master.children()) // 1048576)
    except Exception: pass
stop.set(); time.sleep(1); dur = time.time() - t0
total = sum(codes.values())
print(f"clients={CONC}  duration={dur:.0f}s  requests={total}  throughput={total/dur:.0f} req/s")
print("status codes:", dict(codes), "| client-side errors:", dict(errs) or "none")
print(f"{'type':8} {'count':>6} {'median ms':>10} {'p95 ms':>9} {'max ms':>9}")
for k, v in lat.items():
    v.sort(); print(f"{k:8} {len(v):>6} {statistics.median(v):>10.0f} {v[int(len(v)*.95)-1]:>9.0f} {v[-1]:>9.0f}")
print("peak worker memory (all workers) MB:", peak, "| master alive:", master.is_running())
