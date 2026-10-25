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
MIN_VOLUME_J1     = 100_000_000 # J1 : volume minimum (actions)
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
CSS = """
:root{--bg:#fff;--card:#f6f7f9;--tx:#16181d;--mut:#6b7280;--bd:#e3e6ea;--up:#0a8f4d;--dn:#d23b3b;--ac:#2563eb}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--tx:#e8eaee;--mut:#8b93a1;--bd:#262b35;--up:#3ecf8e;--dn:#f26d6d;--ac:#6ea0ff}}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,sans-serif;padding:16px;max-width:1100px;margin:auto}
h1{font-size:22px;margin:8px 0 2px}h2{font-size:16px;margin:28px 0 4px}
.sub{color:var(--mut);font-size:13px}.hint{color:var(--mut);font-size:13px;margin:0 0 8px}
.tabs{display:flex;gap:6px;margin:16px 0 4px;border-bottom:1px solid var(--bd);overflow-x:auto}
.tab{background:none;border:0;border-bottom:2px solid transparent;color:var(--mut);font:600 15px system-ui,sans-serif;padding:10px 14px;cursor:pointer;white-space:nowrap}
.tab.on{color:var(--tx);border-bottom-color:var(--ac)}
.badge{display:inline-block;min-width:18px;padding:0 6px;margin-left:6px;border-radius:9px;background:var(--card);border:1px solid var(--bd);font-size:12px;color:var(--mut)}
.crit{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:12px 14px;margin:14px 0;font-size:13px;color:var(--mut)}
.crit b{color:var(--tx)}
.wrap{overflow-x:auto;border:1px solid var(--bd);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:14px;white-space:nowrap}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--bd)}
th{background:var(--card);font-weight:600;font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.03em}
tr:last-child td{border-bottom:0}
a{color:var(--ac);font-weight:600;text-decoration:none}
.up{color:var(--up)}.dn{color:var(--dn)}.empty{color:var(--mut);text-align:center;padding:18px}
tr.sig{background:color-mix(in srgb,var(--up) 10%,transparent)}tr.red{background:color-mix(in srgb,var(--dn) 10%,transparent)}
.why{color:var(--mut);white-space:normal}
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
    return f"""
<div class="crit"><b>J1</b> : small cap &lt; {MAX_MARKET_CAP/1e6:g} M$, prix ≥ {MIN_PRICE:g} $, hausse ≥ {MIN_MOVE_PCT} %, volume ≥ {MIN_VOLUME_J1/1e6:g} M (1 à {RUN_MAX_DAYS} jours) &nbsp;·&nbsp;
<b>Consolidation</b> : 1 à {CONSOL_MAX_DAYS} jours, volume ≤ J1 / {VOLUME_RATIO}, clôtures entre {CONSOL_MIN_LEVEL:.0%} de la plage du J1 et son plus haut &nbsp;·&nbsp;
<b>Cassure</b> : clôture au-dessus du plus haut du J1</div>

<h2>En consolidation : à surveiller demain</h2>
<p class="hint">C'est ici que tu prépares ta cassure + retest sur 30 minutes. Niveaux : plafond de consolidation, puis plus haut du J1.</p>
{table(["Ticker","J1","Move J1","Vol J1","Jours conso","Vol conso max","Dernière clôture","Milieu J1","Plafond conso","Haut J1"], cons)}

<h2>Cassures récentes (clôture au-dessus du haut du J1)</h2>
{table(["Ticker","J1","Move J1","Vol J1","Jours conso","Vol conso max","Date cassure","Clôture","Vol cassure","Plafond conso / Haut J1"], cass)}

<h2>Nouveaux J1 (en attente de consolidation)</h2>
{table(["Ticker","J1","Move J1","Vol J1","Jours gros vol","Dernière clôture","Milieu J1","Haut J1"], runs)}

<h2>Invalidés</h2>
{table(["Ticker","J1","Raison"], inv)}"""


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
    return f"""
<div class="crit"><b>Entrée dans la liste</b> : small cap &lt; {FRD_MAX_MARKET_CAP/1e6:g} M$, clôture ≥ {FRD_MIN_PRICE:g} $, volume ≥ {FRD_MIN_VOLUME/1e6:g} M,
clôture au-dessus de la veille, {FRD_RUN_DAYS} jours consécutifs de clôtures en hausse et ≥ {FRD_MIN_GAIN_PCT} % de gain sur {FRD_RUN_DAYS} jours &nbsp;·&nbsp;
<b>First Red Day</b> : un jour clôture sous l'ouverture de la veille</div>

<h2>Nouveaux candidats du jour</h2>
<p class="hint">Titres qui viennent d'entrer dans la liste de surveillance aujourd'hui.</p>
{table(["Ticker","Entré le","Gain "+str(FRD_RUN_DAYS)+" j","Clôture","Jour","Volume","Haut de la course"], new)}

<h2>Surveillance en cours</h2>
<p class="hint">Aucun First Red Day depuis leur entrée dans la liste.</p>
{table(["Ticker","Entré le","Gain "+str(FRD_RUN_DAYS)+" j","Dernière clôture","Jour","Volume","Haut de la course","Vs le haut","Jours"], watch)}

<h2>First Red Day déjà eu lieu</h2>
<p class="hint">Les lignes en rouge sont celles dont le First Red Day s'est produit aujourd'hui.</p>
{table(["Ticker","Entré le","Gain "+str(FRD_RUN_DAYS)+" j","Date FRD","Clôture FRD","Ouverture veille","Haut de la course","Recul vs le haut","Volume FRD"], done)}"""


def page(mcb, frd, updated):
    upd = datetime.fromisoformat(updated).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Screener small caps</title>
<style>{CSS}</style></head><body>
<h1>Screener small caps</h1>
<div class="sub">Dernière mise à jour : {upd}</div>
<div class="tabs" role="tablist">
<button class="tab on" data-t="mcb">Mid Candle Breakout<span class="badge">{len(mcb['consol'])}</span></button>
<button class="tab" data-t="frd">First Red Day<span class="badge">{len(frd['new']) + len(frd['watch'])}</span></button>
</div>
<section id="mcb" class="panel">{panel_mcb(mcb)}</section>
<section id="frd" class="panel" hidden>{panel_frd(frd)}</section>
<script>{JS}</script>
</body></html>"""


if __name__ == "__main__":
    run()
