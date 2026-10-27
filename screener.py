#!/usr/bin/env python3
"""Screener small caps - 2 onglets sur la même page.

Onglet 1 : Mid Candle Breakout
  J1 (gros volume) -> consolidation à volume bas -> cassure du haut du J1.
Onglet 2 : First Red Day
  Liste de surveillance : 3 jours de clôtures en hausse avec +200 % sur 3 jours.
  Dès qu'un jour clôture sous l'ouverture de la veille : "First Red Day a déjà eu lieu".

Mise à jour chaque soir après la clôture (GitHub Actions).
"""
import html, json, sys
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import yfinance as yf
from yfinance import EquityQuery

# ======================= ONGLET 1 : MID CANDLE BREAKOUT =========================
MIN_MOVE_PCT      = 50          # J1 : hausse minimale en %
MAX_MARKET_CAP    = 300_000_000 # small cap : capitalisation max ($)
MIN_VOLUME_J1     = 40_000_000 # J1 : volume minimum (actions)
MIN_PRICE         = 0.50        # J1 : clôture minimum ($) - sinon le titre est ignoré
VOLUME_RATIO      = 10          # consolidation : volume <= volume J1 / 10
RUN_MAX_DAYS      = 2           # le "J1" peut compter 1 ou 2 jours de gros volume
CONSOL_MAX_DAYS   = 5           # consolidation de 1 à 5 jours maximum
CONSOL_MIN_LEVEL  = 0.40        # clôture >= 40 % de la plage (bas->haut) du J1
KEEP_DAYS         = 14          # jours calendaires de suivi

# Titres à suivre à la main : {"TICKER": "date du J1 AAAA-MM-JJ"}
MANUAL_WATCH = {
    "XLAB": "2026-10-16",
}

# ========================= ONGLET 2 : FIRST RED DAY ============================
FRD_RUN_DAYS      = 3           # nombre de jours consécutifs de clôtures en hausse
FRD_MIN_GAIN_PCT  = 200         # gain minimum sur ces jours (clôture vs clôture avant la course)
FRD_MAX_MARKET_CAP= 500_000_000 # small cap : capitalisation max ($)
FRD_MIN_PRICE     = 1.0         # clôture minimum ($)
FRD_MIN_VOLUME    = 10_000_000  # volume minimum (actions)
FRD_KEEP_DAYS     = 10          # jours calendaires de suivi

# Titres à ajouter à la main à la liste : {"TICKER": "date d'entrée AAAA-MM-JJ"}
MANUAL_FRD = {}
# ================================================================================

ROOT = Path(__file__).parent
STATE = ROOT / "state.json"
OUT = ROOT / "docs" / "index.html"


# ================================ DONNÉES ======================================
def bars(symbol):
    h = yf.Ticker(symbol).history(period="2mo", interval="1d", auto_adjust=False)
    return h.dropna(subset=["Close"])


def batch_history(symbols, period="1mo"):
    """Historique journalier de plusieurs titres, par paquets de 100."""
    out = {}
    syms = list(dict.fromkeys(symbols))
    for k in range(0, len(syms), 100):
        chunk = syms[k:k + 100]
        try:
            df = yf.download(chunk, period=period, interval="1d", auto_adjust=False,
                             group_by="ticker", progress=False, threads=True)
        except Exception as ex:
            print("Téléchargement échoué :", ex, file=sys.stderr)
            continue
        for s in chunk:
            try:
                d = df[s] if isinstance(df.columns, pd.MultiIndex) else df
                d = d.dropna(subset=["Close"])
                if len(d):
                    out[s] = d[["Open", "High", "Low", "Close", "Volume"]]
            except Exception:
                pass
    return out


# ============================ MID CANDLE BREAKOUT ==============================
def find_j1_today():
    """Small caps du jour qui respectent les critères J1 (screener Yahoo)."""
    q = EquityQuery("and", [
        EquityQuery("eq", ["region", "us"]),
        EquityQuery("gt", ["percentchange", MIN_MOVE_PCT]),
        EquityQuery("lt", ["intradaymarketcap", MAX_MARKET_CAP]),
        EquityQuery("gt", ["dayvolume", MIN_VOLUME_J1]),
        EquityQuery("gt", ["intradayprice", MIN_PRICE]),
    ])
    res = yf.screen(q, size=250, sortField="percentchange", sortAsc=False)
    return [r["symbol"] for r in res.get("quotes", [])]


def analyze(h, added):
    """Analyse un titre à partir de son historique journalier.
    Retourne un dict avec 'status' parmi : run, consol, cassure, invalide, ignore."""
    dates = [d.strftime("%Y-%m-%d") for d in h.index]
    rows = list(h.itertuples(index=False))
    n = len(rows)
    eligible = [i for i, d in enumerate(dates) if d <= added]
    if not eligible or eligible[-1] == 0:
        return None
    ia = eligible[-1]
    a, prev = rows[ia], rows[ia - 1]
    move = (a.Close / prev.Close - 1) * 100
    res = {"anchor_date": dates[ia], "move": move, "j1_vol": int(a.Volume),
           "status": "invalide", "why": "", "consol_days": 0}
    if a.Close < MIN_PRICE or move < MIN_MOVE_PCT or a.Volume < MIN_VOLUME_J1:
        res.update(status="ignore", why="J1 ne respecte pas les critères")
        return res

    # --- le "run" : J1 + éventuellement un 2e jour vert à gros volume ---
    run, maxvol, i = [ia], a.Volume, ia + 1
    while len(run) < RUN_MAX_DAYS and i < n:
        r = rows[i]
        if r.Close > r.Open and r.Volume > maxvol / VOLUME_RATIO:
            run.append(i)
            maxvol = max(maxvol, r.Volume)
            i += 1
        else:
            break
    low = min(rows[j].Low for j in run)
    high = max(rows[j].High for j in run)
    thr = maxvol / VOLUME_RATIO
    floor = low + CONSOL_MIN_LEVEL * (high - low)
    res.update({"run_days": len(run), "run_end": dates[run[-1]], "j1_vol": int(maxvol),
                "run_low": float(low), "run_high": float(high),
                "mid": float((low + high) / 2), "floor": float(floor)})

    # --- jours suivants ---
    consol = []
    for j in range(i, n):
        r = rows[j]
        if r.Volume <= thr and floor <= r.Close <= high:
            consol.append(j)
            if len(consol) > CONSOL_MAX_DAYS:
                res["why"] = f"consolidation trop longue (> {CONSOL_MAX_DAYS} jours)"
                return res
            continue
        if r.Close > high and consol:
            res.update(status="cassure", break_date=dates[j], break_vol=int(r.Volume),
                       break_close=float(r.Close), consol_days=len(consol),
                       consol_high=float(max(rows[k].High for k in consol)),
                       consol_vol=int(max(rows[k].Volume for k in consol)))
            return res
        if r.Volume > thr:
            res["why"] = f"{dates[j]} : volume {r.Volume/1e6:.1f} M > J1/{VOLUME_RATIO} ({thr/1e6:.1f} M)"
        elif r.Close < floor:
            res["why"] = f"{dates[j]} : clôture {r.Close:.2f} $ sous {CONSOL_MIN_LEVEL:.0%} de la plage J1 ({floor:.2f} $)"
        else:
            res["why"] = f"{dates[j]} : cassure sans consolidation"
        return res

    if consol:
        last = rows[consol[-1]]
        res.update(status="consol", consol_days=len(consol),
                   consol_high=float(max(rows[k].High for k in consol)),
                   consol_vol=int(max(rows[k].Volume for k in consol)),
                   last_close=float(last.Close), last_date=dates[consol[-1]])
    else:
        res.update(status="run", last_close=float(rows[run[-1]].Close), last_date=dates[run[-1]])
    return res


# ================================ FIRST RED DAY ================================
def find_frd_candidates():
    """Small caps du jour : en hausse, prix et volume minimum (screener Yahoo)."""
    syms = []
    for off in range(0, 1000, 250):
        q = EquityQuery("and", [
            EquityQuery("eq", ["region", "us"]),
            EquityQuery("gt", ["percentchange", 0]),
            EquityQuery("lt", ["intradaymarketcap", FRD_MAX_MARKET_CAP]),
            EquityQuery("gte", ["intradayprice", FRD_MIN_PRICE]),
            EquityQuery("gte", ["dayvolume", FRD_MIN_VOLUME]),
        ])
        res = yf.screen(q, offset=off, size=250, sortField="percentchange", sortAsc=False)
        quotes = res.get("quotes", [])
        syms += [r["symbol"] for r in quotes]
        if len(quotes) < 250:
            break
    return syms


def frd_gain_at(rows, i):
    """Gain en % sur FRD_RUN_DAYS jours si le titre respecte les critères au jour i, sinon None."""
    n = FRD_RUN_DAYS
    if i < n:
        return None
    c = [rows[k].Close for k in range(i - n, i + 1)]
    if not all(c[k + 1] > c[k] for k in range(n)):
        return None                       # pas n clôtures consécutives en hausse
    gain = (c[-1] / c[0] - 1) * 100
    if gain < FRD_MIN_GAIN_PCT:
        return None
    if rows[i].Close < FRD_MIN_PRICE or rows[i].Volume < FRD_MIN_VOLUME:
        return None
    return gain


def frd_qualify_last(h):
    rows = list(h.itertuples(index=False))
    i = len(rows) - 1
    g = frd_gain_at(rows, i)
    if g is None:
        return None
    return {"date": h.index[i].strftime("%Y-%m-%d"), "gain": g}


def analyze_frd(h, added):
    """Suit un titre de la liste depuis sa date d'entrée.
    status : 'new' (entré aujourd'hui), 'watch' (en surveillance) ou 'frd' (First Red Day déjà eu lieu)."""
    dates = [d.strftime("%Y-%m-%d") for d in h.index]
    rows = list(h.itertuples(index=False))
    n = len(rows)
    if added not in dates:
        return None
    ia = dates.index(added)
    if ia < FRD_RUN_DAYS:
        return None
    gain = (rows[ia].Close / rows[ia - FRD_RUN_DAYS].Close - 1) * 100
    start = ia - FRD_RUN_DAYS + 1
    res = {"added": added, "gain": gain, "entry_close": float(rows[ia].Close)}

    # premier jour après l'entrée qui clôture sous l'ouverture de la veille
    for d in range(ia + 1, n):
        if rows[d].Close < rows[d - 1].Open:
            peak = max(r.High for r in rows[start:d])
            res.update(status="frd", frd_date=dates[d], frd_close=float(rows[d].Close),
                       prev_open=float(rows[d - 1].Open), peak=float(peak),
                       drop=(rows[d].Close / peak - 1) * 100, frd_vol=int(rows[d].Volume),
                       is_today=(d == n - 1))
            return res

    last = rows[-1]
    peak = max(r.High for r in rows[start:])
    res.update(status="new" if ia == n - 1 else "watch", last_close=float(last.Close),
               day_pct=(last.Close / rows[-2].Close - 1) * 100, vol=int(last.Volume),
               peak=float(peak), from_peak=(last.Close / peak - 1) * 100, days=n - 1 - ia)
    return res


# ================================== PIPELINE ===================================
def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"watch": []}


def run_mcb(state, today):
    known = {w["symbol"] for w in state["watch"]}
    for s, d in MANUAL_WATCH.items():
        if s not in known:
            state["watch"].append({"symbol": s, "added": d})
            known.add(s)
    try:
        for s in find_j1_today():
            if s not in known:
                state["watch"].append({"symbol": s, "added": today})
                known.add(s)
    except Exception as ex:
        print("Screener Yahoo indisponible (MCB) :", ex, file=sys.stderr)

    groups = {"cassure": [], "consol": [], "run": [], "invalide": []}
    keep = []
    for w in state["watch"]:
        age = (datetime.fromisoformat(today) - datetime.fromisoformat(w["added"])).days
        if age > KEEP_DAYS:
            continue
        try:
            e = analyze(bars(w["symbol"]), w["added"])
        except Exception as ex:
            print("Erreur", w["symbol"], ex, file=sys.stderr)
            keep.append(w)
            continue
        if e and e["status"] == "ignore":
            continue  # pas gardé dans la watchlist
        keep.append(w)
        if e:
            e["symbol"] = w["symbol"]
            groups[e["status"]].append(e)
    state["watch"] = keep
    return groups


def run_frd(state, today):
    frd = state.setdefault("frd_watch", [])
    known = {w["symbol"] for w in frd}
    for s, d in MANUAL_FRD.items():
        if s not in known:
            frd.append({"symbol": s, "added": d})
            known.add(s)
    cands = []
    try:
        cands = find_frd_candidates()
    except Exception as ex:
        print("Screener Yahoo indisponible (FRD) :", ex, file=sys.stderr)

    hist = batch_history(cands + [w["symbol"] for w in frd])
    for s in cands:                       # nouveaux entrants du jour
        if s in known or s not in hist:
            continue
        q = frd_qualify_last(hist[s])
        if q:
            frd.append({"symbol": s, "added": q["date"]})
            known.add(s)

    groups = {"new": [], "watch": [], "frd": []}
    keep = []
    for w in frd:
        age = (datetime.fromisoformat(today) - datetime.fromisoformat(w["added"])).days
        if age > FRD_KEEP_DAYS:
            continue
        keep.append(w)
        h = hist.get(w["symbol"])
        if h is None:
            continue
        e = analyze_frd(h, w["added"])
        if e:
            e["symbol"] = w["symbol"]
            groups[e["status"]].append(e)
    state["frd_watch"] = keep
    return groups


def run():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state = load_state()
    mcb = run_mcb(state, today)
    try:
        frd = run_frd(state, today)
    except Exception as ex:
        print("Onglet First Red Day en erreur :", ex, file=sys.stderr)
        frd = {"new": [], "watch": [], "frd": []}
    state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    STATE.write_text(json.dumps(state, indent=1))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(page(mcb, frd, state["updated"]))
    print("MCB", {k: len(v) for k, v in mcb.items()}, "| FRD", {k: len(v) for k, v in frd.items()})


# ================================== HTML =======================================
# Style repris du CMP Journal : bandeau marine, fond crème, cartes blanches, titres en Playfair Display.
FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=Lato:wght@300;400;700&'
         'family=Playfair+Display:wght@400;500;600&display=swap" rel="stylesheet">')

CSS = """
:root{--navy:#1a2744;--cream:#f5f0e8;--green:#1a7a4a;--red:#b23a2e;--mut:#7a8599;--blue:#2d4a8a;--bd:#ece7de}
*{box-sizing:border-box}
body{margin:0;background:var(--cream);color:var(--navy);font:15px/1.5 'Lato',system-ui,sans-serif}
.wm{position:fixed;inset:0;background:url(logo.png) center 38%/560px no-repeat;opacity:.07;pointer-events:none}
.top{position:relative;background:var(--navy);color:#fff;padding:0 28px;height:62px;display:flex;align-items:center;justify-content:space-between;gap:12px}
.brand{font:500 22px 'Playfair Display',Georgia,serif}
.brand small{font:700 11px 'Lato',sans-serif;letter-spacing:.18em;color:#8c97b2;margin-left:14px;text-transform:uppercase}
.sync{font-size:12px;color:#aab3c8;white-space:nowrap}
.sync:before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:#4fb286;margin-right:7px}
nav.tabs{position:relative;background:#fff;border-bottom:1px solid var(--bd);padding:0 22px;display:flex;gap:4px;overflow-x:auto}
.tab{background:none;border:0;border-bottom:2px solid transparent;color:var(--mut);font:700 12px 'Lato',sans-serif;letter-spacing:.14em;text-transform:uppercase;padding:22px 18px 18px;cursor:pointer;white-space:nowrap}
.tab:hover{color:var(--navy)}
.tab.on{color:var(--navy);border-bottom-color:var(--navy)}
.badge{display:inline-block;min-width:20px;padding:1px 7px;margin-left:8px;border-radius:10px;background:#eef0f4;color:var(--mut);font-size:11px;letter-spacing:0}
.tab.on .badge{background:var(--navy);color:#fff}
main{position:relative;max-width:1180px;margin:0 auto;padding:26px 28px 70px}
.pill{display:inline-block;background:#fff;border:1px solid #cdd0d6;color:var(--blue);border-radius:20px;padding:6px 16px;font:700 11px 'Lato',sans-serif;letter-spacing:.14em;text-transform:uppercase;box-shadow:0 1px 3px rgba(26,39,68,.06)}
h1{font:400 34px/1.2 'Playfair Display',Georgia,serif;margin:22px 0 4px}
.lead{color:var(--mut);margin:0 0 22px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:18px}
.stat,.card{background:#fff;border-radius:14px;box-shadow:0 2px 12px rgba(26,39,68,.07);border:1px solid rgba(26,39,68,.04)}
.stat{padding:18px 20px}
.stat .k{font:700 11px 'Lato',sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--mut)}
.stat .v{font:400 32px/1.25 'Playfair Display',Georgia,serif;margin:2px 0}
.stat .s{font-size:12px;color:var(--mut)}
.card{padding:22px 24px;margin-bottom:18px}
.card h2{font:700 13px 'Lato',sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--mut);margin:0 0 4px}
.hint{color:var(--mut);font-size:13px;margin:0 0 14px}
.crit{font-size:13px;color:var(--mut);line-height:1.7}
.crit b{color:var(--navy)}
.wrap{overflow-x:auto;margin-top:10px}
table{border-collapse:collapse;width:100%;font-size:14px;white-space:nowrap}
th,td{padding:10px 14px 10px 0;text-align:left;border-bottom:1px solid var(--bd)}
th{font:700 11px 'Lato',sans-serif;color:var(--mut);text-transform:uppercase;letter-spacing:.12em}
tr:last-child td{border-bottom:0}
a{color:var(--blue);font-weight:700;text-decoration:none}
a:hover{text-decoration:underline}
.up{color:var(--green)}.dn{color:var(--red)}
.empty{color:var(--mut);text-align:center;padding:20px 0}
tr.sig td{background:#edf5f1}tr.red td{background:#faeceb}
tr.sig td:first-child,tr.red td:first-child{padding-left:12px}
.why{color:var(--mut);white-space:normal}
@media(max-width:640px){.top,nav.tabs{padding-left:16px;padding-right:16px}main{padding:20px 16px 60px}h1{font-size:28px}.card{padding:18px 16px}.sync{display:none}}
"""

JS = """
(function(){var t=document.querySelectorAll('.tab'),p=document.querySelectorAll('.panel');
function show(id){t.forEach(function(b){b.classList.toggle('on',b.dataset.t===id)});
p.forEach(function(s){s.hidden=s.id!==id});try{history.replaceState(null,'','#'+id)}catch(e){}}
t.forEach(function(b){b.addEventListener('click',function(){show(b.dataset.t)})});
var h='';try{h=(location.hash||'').slice(1)}catch(e){}
if(h==='frd'||h==='mcb')show(h);})();
"""


def vol(v):
    return f"{v/1e6:,.1f} M"


def link(sym):
    s = html.escape(sym)
    return f'<a href="https://finance.yahoo.com/quote/{s}" target="_blank" rel="noopener">{s}</a>'


def pct(v):
    cls = "up" if v >= 0 else "dn"
    return f"<span class='{cls}'>{v:+.1f} %</span>"


def table(head, rows_html):
    body = "".join(rows_html) or f'<tr><td colspan="{len(head)}" class="empty">Aucun titre</td></tr>'
    th = "".join(f"<th>{h}</th>" for h in head)
    return f'<div class="wrap"><table><tr>{th}</tr>{body}</table></div>'


def card(title, hint, inner):
    h = f'<p class="hint">{hint}</p>' if hint else ""
    return f'<div class="card"><h2>{title}</h2>{h}{inner}</div>'


def stats(items):
    out = "".join(f'<div class="stat"><div class="k">{k}</div><div class="v {c}">{v}</div>'
                  f'<div class="s">{s}</div></div>' for k, v, s, c in items)
    return f'<div class="stats">{out}</div>'


def panel_mcb(g):
    cass = [f"<tr class='sig'><td>{link(e['symbol'])}</td><td>{e['anchor_date']}</td>"
            f"<td class='up'>{e['move']:+.0f} %</td><td>{vol(e['j1_vol'])}</td>"
            f"<td>{e['consol_days']}</td><td>{vol(e['consol_vol'])}</td>"
            f"<td>{e['break_date']}</td><td>${e['break_close']:.2f}</td><td>{vol(e['break_vol'])}</td>"
            f"<td>${e['consol_high']:.2f} / ${e['run_high']:.2f}</td></tr>"
            for e in sorted(g["cassure"], key=lambda x: x["break_date"], reverse=True)]
    cons = [f"<tr><td>{link(e['symbol'])}</td><td>{e['anchor_date']}</td>"
            f"<td class='up'>{e['move']:+.0f} %</td><td>{vol(e['j1_vol'])}</td>"
            f"<td>{e['consol_days']}</td><td>{vol(e['consol_vol'])} ({e['j1_vol']/max(e['consol_vol'],1):.0f}x moins)</td>"
            f"<td>${e['last_close']:.2f}</td><td>${e['mid']:.2f}</td>"
            f"<td>${e['consol_high']:.2f}</td><td>${e['run_high']:.2f}</td></tr>"
            for e in sorted(g["consol"], key=lambda x: x["anchor_date"], reverse=True)]
    runs = [f"<tr><td>{link(e['symbol'])}</td><td>{e['anchor_date']}</td>"
            f"<td class='up'>{e['move']:+.0f} %</td><td>{vol(e['j1_vol'])}</td>"
            f"<td>{e['run_days']}</td><td>${e['last_close']:.2f}</td>"
            f"<td>${e['mid']:.2f}</td><td>${e['run_high']:.2f}</td></tr>"
            for e in sorted(g["run"], key=lambda x: x["anchor_date"], reverse=True)]
    inv = [f"<tr><td>{link(e['symbol'])}</td><td>{e['anchor_date']}</td>"
           f"<td class='why'>{html.escape(e['why'])}</td></tr>"
           for e in sorted(g["invalide"], key=lambda x: x["anchor_date"], reverse=True)]
    crit = (f"<div class='crit'><b>J1</b> : small cap &lt; {MAX_MARKET_CAP/1e6:g} M$, prix ≥ {MIN_PRICE:g} $, "
            f"hausse ≥ {MIN_MOVE_PCT} %, volume ≥ {MIN_VOLUME_J1/1e6:g} M (1 à {RUN_MAX_DAYS} jours)<br>"
            f"<b>Consolidation</b> : 1 à {CONSOL_MAX_DAYS} jours, volume ≤ J1 / {VOLUME_RATIO}, clôtures entre "
            f"{CONSOL_MIN_LEVEL:.0%} de la plage du J1 et son plus haut<br>"
            f"<b>Cassure</b> : clôture au-dessus du plus haut du J1</div>")
    return (
        '<span class="pill">📊 Screener</span><h1>Mid Candle Breakout</h1>'
        '<p class="lead">Small caps : J1 à gros volume, consolidation à volume bas, cassure du haut du J1</p>'
        + stats([("En consolidation", len(g["consol"]), "à surveiller demain", ""),
                 ("Cassures récentes", len(g["cassure"]), "clôture au-dessus du haut du J1", "up" if g["cassure"] else ""),
                 ("Nouveaux J1", len(g["run"]), "en attente de consolidation", ""),
                 ("Invalidés", len(g["invalide"]), "consolidation échouée", "")])
        + card("Critères", "", crit)
        + card("En consolidation : à surveiller demain",
               "C'est ici que tu prépares ta cassure + retest sur 30 minutes. Niveaux : plafond de consolidation, puis plus haut du J1.",
               table(["Ticker", "J1", "Move J1", "Vol J1", "Jours conso", "Vol conso max", "Dernière clôture",
                      "Milieu J1", "Plafond conso", "Haut J1"], cons))
        + card("Cassures récentes", "Clôture au-dessus du plus haut du J1 après la consolidation.",
               table(["Ticker", "J1", "Move J1", "Vol J1", "Jours conso", "Vol conso max", "Date cassure",
                      "Clôture", "Vol cassure", "Plafond conso / Haut J1"], cass))
        + card("Nouveaux J1", "En attente de consolidation.",
               table(["Ticker", "J1", "Move J1", "Vol J1", "Jours gros vol", "Dernière clôture", "Milieu J1", "Haut J1"], runs))
        + card("Invalidés", "", table(["Ticker", "J1", "Raison"], inv)))


def panel_frd(g):
    new = [f"<tr class='sig'><td>{link(e['symbol'])}</td><td>{e['added']}</td>"
           f"<td class='up'>{e['gain']:+.0f} %</td><td>${e['last_close']:.2f}</td>"
           f"<td>{pct(e['day_pct'])}</td><td>{vol(e['vol'])}</td><td>${e['peak']:.2f}</td></tr>"
           for e in sorted(g["new"], key=lambda x: x["gain"], reverse=True)]
    watch = [f"<tr><td>{link(e['symbol'])}</td><td>{e['added']}</td>"
             f"<td class='up'>{e['gain']:+.0f} %</td><td>${e['last_close']:.2f}</td>"
             f"<td>{pct(e['day_pct'])}</td><td>{vol(e['vol'])}</td><td>${e['peak']:.2f}</td>"
             f"<td>{pct(e['from_peak'])}</td><td>{e['days']}</td></tr>"
             for e in sorted(g["watch"], key=lambda x: x["added"], reverse=True)]
    done = [f"<tr class='{'red' if e['is_today'] else ''}'><td>{link(e['symbol'])}</td><td>{e['added']}</td>"
            f"<td class='up'>{e['gain']:+.0f} %</td><td>{e['frd_date']}</td>"
            f"<td>${e['frd_close']:.2f}</td><td>${e['prev_open']:.2f}</td><td>${e['peak']:.2f}</td>"
            f"<td>{pct(e['drop'])}</td><td>{vol(e['frd_vol'])}</td></tr>"
            for e in sorted(g["frd"], key=lambda x: x["frd_date"], reverse=True)]
    today_frd = sum(1 for e in g["frd"] if e["is_today"])
    crit = (f"<div class='crit'><b>Entrée dans la liste</b> : small cap &lt; {FRD_MAX_MARKET_CAP/1e6:g} M$, "
            f"clôture ≥ {FRD_MIN_PRICE:g} $, volume ≥ {FRD_MIN_VOLUME/1e6:g} M, clôture au-dessus de la veille, "
            f"{FRD_RUN_DAYS} jours consécutifs de clôtures en hausse et ≥ {FRD_MIN_GAIN_PCT} % de gain sur {FRD_RUN_DAYS} jours<br>"
            f"<b>First Red Day</b> : un jour clôture sous l'ouverture de la veille</div>")
    gl = f"Gain {FRD_RUN_DAYS} j"
    return (
        '<span class="pill">📊 Screener</span><h1>First Red Day</h1>'
        f'<p class="lead">Small caps : course de {FRD_RUN_DAYS} jours (+{FRD_MIN_GAIN_PCT} %), puis premier jour sous l\'ouverture de la veille</p>'
        + stats([("Nouveaux candidats", len(g["new"]), "entrés aujourd'hui", "up" if g["new"] else ""),
                 ("En surveillance", len(g["watch"]), "pas encore de First Red Day", ""),
                 ("FRD aujourd'hui", today_frd, "premier jour rouge ce soir", "dn" if today_frd else ""),
                 ("FRD déjà eu lieu", len(g["frd"]), "sur les derniers jours", "")])
        + card("Critères", "", crit)
        + card("Nouveaux candidats du jour", "Titres qui viennent d'entrer dans la liste de surveillance aujourd'hui.",
               table(["Ticker", "Entré le", gl, "Clôture", "Jour", "Volume", "Haut de la course"], new))
        + card("Surveillance en cours", "Aucun First Red Day depuis leur entrée dans la liste.",
               table(["Ticker", "Entré le", gl, "Dernière clôture", "Jour", "Volume", "Haut de la course",
                      "Vs le haut", "Jours"], watch))
        + card("First Red Day déjà eu lieu", "Les lignes en rouge sont celles dont le First Red Day s'est produit aujourd'hui.",
               table(["Ticker", "Entré le", gl, "Date FRD", "Clôture FRD", "Ouverture veille", "Haut de la course",
                      "Recul vs le haut", "Volume FRD"], done)))


def page(mcb, frd, updated):
    upd = datetime.fromisoformat(updated).strftime("%Y-%m-%d %H:%M UTC")
    wm = '<div class="wm"></div>' if (ROOT / "docs" / "logo.png").exists() else ""
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CMP Screener</title>
{FONTS}<style>{CSS}</style></head><body>{wm}
<header class="top"><div class="brand">CMP Screener<small>Small caps</small></div>
<div class="sync">Mis à jour {upd}</div></header>
<nav class="tabs" role="tablist">
<button class="tab on" data-t="mcb">Mid Candle Breakout<span class="badge">{len(mcb['consol'])}</span></button>
<button class="tab" data-t="frd">First Red Day<span class="badge">{len(frd['new']) + len(frd['watch'])}</span></button>
</nav>
<main>
<section id="mcb" class="panel">{panel_mcb(mcb)}</section>
<section id="frd" class="panel" hidden>{panel_frd(frd)}</section>
</main>
<script>{JS}</script>
</body></html>"""


if __name__ == "__main__":
    run()
