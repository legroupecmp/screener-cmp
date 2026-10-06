#!/usr/bin/env python3
"""Screener "Mid Candle Breakout" (small caps) - mise à jour quotidienne.

Jour 1 (J1) : small cap qui a bougé de +X % avec un gros volume.
Jour 2 (J2) : volume <= volume J1 / 20 et petit gap down à l'ouverture.

Chaque soir après la clôture :
  1. on repère les J1 du jour (ils iront dans « À surveiller demain »)
  2. on vérifie les J1 des jours précédents : si J2 respecte les critères -> SIGNAL
"""
import html, json, sys
from datetime import datetime, timezone
from pathlib import Path
import yfinance as yf
from yfinance import EquityQuery

# ============================ CRITÈRES (modifiables) ============================
MIN_MOVE_PCT      = 50          # J1 : hausse minimale en %
MAX_MARKET_CAP    = 300_000_000 # small cap : capitalisation max ($)
MIN_VOLUME_J1     = 100_000_000 # J1 : volume minimum (actions)
VOLUME_RATIO      = 20          # J2 : volume <= volume J1 / 20
GAP_MIN_PCT       = -15.0       # J2 : gap down le plus profond accepté
GAP_MAX_PCT       = -2.0        # J2 : gap down le moins profond accepté
KEEP_DAYS         = 10          # historique conservé (jours calendaires)
# ================================================================================

ROOT = Path(__file__).parent
STATE = ROOT / "state.json"
OUT = ROOT / "docs" / "index.html"


def find_j1_today():
    """Small caps du jour qui respectent les critères J1 (via le screener Yahoo)."""
    q = EquityQuery("and", [
        EquityQuery("eq", ["region", "us"]),
        EquityQuery("gt", ["percentchange", MIN_MOVE_PCT]),
        EquityQuery("lt", ["intradaymarketcap", MAX_MARKET_CAP]),
        EquityQuery("gt", ["dayvolume", MIN_VOLUME_J1]),
    ])
    res = yf.screen(q, size=250, sortField="percentchange", sortAsc=False)
    return [r["symbol"] for r in res.get("quotes", [])]


def bars(symbol):
    h = yf.Ticker(symbol).history(period="15d", interval="1d", auto_adjust=False)
    return h.dropna(subset=["Close"])


def eval_pair(h, j1_date):
    """h = DataFrame journalier. Retourne dict J1/J2 ou None si pas évaluable."""
    h = h.copy()
    h.index = [d.strftime("%Y-%m-%d") for d in h.index]
    if j1_date not in h.index:
        return None
    i = list(h.index).index(j1_date)
    if i == 0:
        return None
    prev, j1 = h.iloc[i - 1], h.iloc[i]
    out = {
        "j1_date": j1_date,
        "j1_move": (j1.Close / prev.Close - 1) * 100,
        "j1_close": float(j1.Close),
        "j1_vol": int(j1.Volume),
        "j2": None,
    }
    if i + 1 < len(h):
        j2 = h.iloc[i + 1]
        out["j2"] = {
            "date": h.index[i + 1],
            "open": float(j2.Open),
            "close": float(j2.Close),
            "gap": (j2.Open / j1.Close - 1) * 100,
            "vol": int(j2.Volume),
            "vol_ratio": (j1.Volume / j2.Volume) if j2.Volume else float("inf"),
        }
    return out


def verdict(e):
    ok_j1 = e["j1_move"] >= MIN_MOVE_PCT and e["j1_vol"] >= MIN_VOLUME_J1
    if not ok_j1:
        return "j1_invalide", ["J1 ne respecte plus les critères"]
    if e["j2"] is None:
        return "attente", []
    j2, why = e["j2"], []
    if not (GAP_MIN_PCT <= j2["gap"] <= GAP_MAX_PCT):
        why.append(f"gap {j2['gap']:+.1f} % hors [{GAP_MIN_PCT:g} ; {GAP_MAX_PCT:g}]")
    if j2["vol_ratio"] < VOLUME_RATIO:
        why.append(f"volume seulement {j2['vol_ratio']:.1f}x plus bas (< {VOLUME_RATIO}x)")
    return ("signal" if not why else "rejete"), why


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"watch": []}


def run():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state = load_state()
    known = {(w["symbol"]) for w in state["watch"]}

    # 1) nouveaux J1 du jour
    try:
        new = find_j1_today()
    except Exception as ex:
        print("Screener Yahoo indisponible :", ex, file=sys.stderr)
        new = []
    for s in new:
        if s not in known:
            state["watch"].append({"symbol": s, "added": today})
            known.add(s)

    # 2) évaluer tout ce qui est suivi
    signals, waiting, rejected = [], [], []
    keep = []
    for w in state["watch"]:
        age = (datetime.fromisoformat(today) - datetime.fromisoformat(w["added"])).days
        if age > KEEP_DAYS:
            continue
        try:
            h = bars(w["symbol"])
        except Exception as ex:
            print("Erreur", w["symbol"], ex, file=sys.stderr)
            keep.append(w)
            continue
        # le J1 = dernière barre avant J2, retrouvé par la date d'ajout (la barre du jour d'ajout)
        e = eval_pair(h, w.get("j1_date") or _guess_j1(h, w["added"]))
        if not e:
            keep.append(w)
            continue
        w["j1_date"] = e["j1_date"]
        keep.append(w)
        e["symbol"] = w["symbol"]
        v, why = verdict(e)
        e["why"] = why
        {"signal": signals, "attente": waiting}.get(v, rejected).append(e)
    state["watch"] = keep
    state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    STATE.write_text(json.dumps(state, indent=1))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(render(signals, waiting, rejected, state["updated"]))
    print(f"{len(signals)} signaux, {len(waiting)} à surveiller, {len(rejected)} rejetés")


def _guess_j1(h, added):
    """Dernière barre dont la date <= date d'ajout."""
    dates = [d.strftime("%Y-%m-%d") for d in h.index]
    ok = [d for d in dates if d <= added]
    return ok[-1] if ok else added


# ------------------------------- HTML -------------------------------
def fmt_vol(v):
    return f"{v/1e6:,.1f} M" if v >= 1e6 else f"{v:,}"


def link(sym):
    s = html.escape(sym)
    return f'<a href="https://finance.yahoo.com/quote/{s}" target="_blank" rel="noopener">{s}</a>'


def render(signals, waiting, rejected, updated):
    def rows(items, kind):
        if not items:
            return '<tr><td colspan="9" class="empty">Aucun titre</td></tr>'
        out = []
        for e in items:
            j2 = e["j2"]
            if kind == "wait":
                out.append(f"<tr><td>{link(e['symbol'])}</td><td>{e['j1_date']}</td>"
                           f"<td class='up'>{e['j1_move']:+.1f} %</td><td>{fmt_vol(e['j1_vol'])}</td>"
                           f"<td>${e['j1_close']:.2f}</td></tr>")
            else:
                cls = "sig" if kind == "sig" else ""
                extra = "" if kind == "sig" else f"<td class='why'>{html.escape(' ; '.join(e['why']))}</td>"
                out.append(f"<tr class='{cls}'><td>{link(e['symbol'])}</td><td>{e['j1_date']}</td>"
                           f"<td class='up'>{e['j1_move']:+.1f} %</td><td>{fmt_vol(e['j1_vol'])}</td>"
                           f"<td>${e['j1_close']:.2f}</td><td>{j2['date']}</td>"
                           f"<td class='down'>{j2['gap']:+.1f} %</td><td>{fmt_vol(j2['vol'])}</td>"
                           f"<td>{j2['vol_ratio']:.0f}x</td>{extra}</tr>")
        return "".join(out)

    full_head = ("<th>Ticker</th><th>J1</th><th>Move J1</th><th>Vol J1</th><th>Clôture J1</th>"
                 "<th>J2</th><th>Gap J2</th><th>Vol J2</th><th>Ratio vol</th>")
    upd = datetime.fromisoformat(updated).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Screener Mid Candle Breakout</title>
<style>
:root{{--bg:#fff;--card:#f6f7f9;--tx:#16181d;--mut:#6b7280;--bd:#e3e6ea;--up:#0a8f4d;--dn:#d23b3b;--ac:#2563eb}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0f1115;--card:#171a21;--tx:#e8eaee;--mut:#8b93a1;--bd:#262b35;--up:#3ecf8e;--dn:#f26d6d;--ac:#6ea0ff}}}}
body{{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,sans-serif;padding:16px;max-width:1000px;margin:auto}}
h1{{font-size:22px;margin:8px 0 2px}}h2{{font-size:16px;margin:28px 0 8px}}
.sub{{color:var(--mut);font-size:13px}}
.crit{{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:12px 14px;margin:14px 0;font-size:13px;color:var(--mut)}}
.crit b{{color:var(--tx)}}
.wrap{{overflow-x:auto;border:1px solid var(--bd);border-radius:10px}}
table{{border-collapse:collapse;width:100%;font-size:14px;white-space:nowrap}}
th,td{{padding:8px 12px;text-align:left;border-bottom:1px solid var(--bd)}}
th{{background:var(--card);font-weight:600;font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.03em}}
tr:last-child td{{border-bottom:0}}
a{{color:var(--ac);font-weight:600;text-decoration:none}}
.up{{color:var(--up)}}.down{{color:var(--dn)}}.empty{{color:var(--mut);text-align:center;padding:18px}}
tr.sig{{background:color-mix(in srgb,var(--up) 10%,transparent)}}.why{{color:var(--mut);white-space:normal}}
</style></head><body>
<h1>Screener Mid Candle Breakout</h1>
<div class="sub">Dernière mise à jour : {upd}</div>
<div class="crit"><b>J1</b> : small cap &lt; {MAX_MARKET_CAP/1e6:g} M$, hausse ≥ {MIN_MOVE_PCT} %, volume ≥ {MIN_VOLUME_J1/1e6:g} M &nbsp;·&nbsp;
<b>J2</b> : gap down entre {GAP_MAX_PCT:g} % et {GAP_MIN_PCT:g} %, volume ≤ J1 / {VOLUME_RATIO}</div>
<h2>Signaux (J2 confirmé)</h2>
<div class="wrap"><table><tr>{full_head}</tr>{rows(signals, "sig")}</table></div>
<h2>À surveiller demain (J1 validé aujourd'hui)</h2>
<div class="wrap"><table><tr><th>Ticker</th><th>J1</th><th>Move J1</th><th>Vol J1</th><th>Clôture J1</th></tr>{rows(waiting, "wait")}</table></div>
<h2>J2 récents non retenus</h2>
<div class="wrap"><table><tr>{full_head}<th>Raison</th></tr>{rows(rejected, "rej")}</table></div>
</body></html>"""


if __name__ == "__main__":
    run()
