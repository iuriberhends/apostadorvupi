r"""
bot_vupi_battle.py — Vupi eBasket: Over HT + Over Jogador A + Over Jogador B.

TODAS as estratégias usam o H2H (last_50 do TipManager, ~100 jogos):
  • HT (mt=68):   ult10 score_ht_a+b ≥ X%  E  ult5 ≥ Y%
  • PA (mt=227):  ult20 score_ft do PLAYER A ≥ X%  E  ult5 ≥ Y%   linha ≤ 65.5
  • PB (mt=228):  ult20 score_ft do PLAYER B ≥ X%  E  ult5 ≥ Y%   linha ≤ 65.5

LOOP a cada SLEEP_LOOP:
  1. Lista live eBasket Vupi
  2. Pra evento NOVO Battle/Conference: H2H → calcula linha alvo de cada estrat
  3. Pra cada (jogo, estrat) com linha alvo: pega detalhe → checa linha live
     se linha_live ≤ alvo E < ultima → APOSTA progressiva
"""

import asyncio
import csv
import gzip
import json
import re
import string
import sys
import time
import random
import base64
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
import requests
from curl_cffi import requests as cfr
from Crypto.Cipher import AES
from playwright.async_api import async_playwright


# ╔══════════════════════════════════════════════════════════════╗
# ║  CONFIG                                                       ║
# ╚══════════════════════════════════════════════════════════════╝

CDP_PORT = 9231
EMAIL_VUPI = "msdj730@gmail.com"
SENHA_VUPI = "Haniel123"

SLEEP_LOOP         = 0.5
ACEITA_MUDANCA_ODD = True

COOLDOWN_TENTATIVA      = 1
MAX_FALHAS_SEGUIDAS     = 9999
ERROS_IGNORAR_BLOQUEIO  = {4, "4"}  # errorType=4 = linha mudou (race normal)

# ─────── ESTRATÉGIAS ───────
# Cada chave = uma estratégia. Por liga define wrN/wr5 (em cima do H2H).
ESTRATEGIAS = {
    "ht": {
        "tipo": "Over HT (1º tempo total)",
        "market_type_id": 68,
        "tempos_ok": ("1ª parte",),
        "ligas": {
            "battle":     {"n": 10, "wrN_min": 0.70, "wr5_min": 0.60,
                           "linha_max": 999, "stake": 1.0, "max_ent": 15},
            "conference": {"n": 10, "wrN_min": 0.60, "wr5_min": 0.80,
                           "linha_max": 999, "stake": 1.0, "max_ent": 15},
        },
    },
    "pa": {
        "tipo": "Over Jogador A (jogo todo)",
        "market_type_id": 227,
        "tempos_ok": ("1ª parte", "2ª parte", "3ª parte"),
        "ligas": {
            "battle": {"n": 20, "wrN_min": 0.70, "wr5_min": 0.80,
                       "linha_max": 65.5, "stake": 1.0, "max_ent": 5},
        },
    },
    "pb": {
        "tipo": "Over Jogador B (jogo todo)",
        "market_type_id": 228,
        "tempos_ok": ("1ª parte", "2ª parte", "3ª parte"),
        "ligas": {
            "battle": {"n": 20, "wrN_min": 0.70, "wr5_min": 0.80,
                       "linha_max": 65.5, "stake": 1.0, "max_ent": 5},
        },
    },
}

# TipManager
SUPA_URL = "https://jnurxezspleufiooyekw.supabase.co"
SUPA_KEY = "sb_publishable_kApTLSOlncxHBxBkred3cA_XGCNRVFW"
TM_EMAIL = "guilhermeastorrevieira@gmail.com"
TM_SENHA = "Gui!28091991"
TM_API_H2H = "https://h2h.tipmanager.xyz:2087/v2/ebasket_encrypted"
TM_AES_KEY = b"bd084427da1431cc42b8c8c4c0b5fe3a"
TM_APP_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJleHAiOjIwMDk5NDcwNjgsImlhdCI6MTY5ODkwNzA2OCwidXNlciI6InRpcG1hbmFnZXIifQ."
    "icE5hbAKg9-V_DjxlXZem-hmmo5NIPsudULFD-nOwCk"
)
TM_HEADERS_PLAYERS = {
    "Authorization": f"Bearer {TM_APP_TOKEN}",
    "Content-Type": "application/json",
    "Origin":  "https://tipmanager.net",
    "Referer": "https://tipmanager.net/",
}

# Vupi
VP_BFF   = "https://bff.vupi.bet.br"
VP_FRONT = "https://sb2frontend-altenar2.biahosted.com"
VP_AUTH  = "https://sb2auth-altenar2.biahosted.com"
VP_BET   = "https://sb2betgateway-altenar2.biahosted.com"

VP_PARAMS = {
    "culture": "pt-BR", "timezoneOffset": 180, "integration": "vupi",
    "deviceType": 1, "numFormat": "en-GB", "countryCode": "BR",
}
VP_HEADERS = {
    "Origin": "https://www.vupi.bet.br",
    "Referer": "https://www.vupi.bet.br/",
    "Accept": "application/json, text/plain, */*",
}

LOG_CSV = Path("apostas_log.csv")


# ╔══════════════════════════════════════════════════════════════╗
# ║  DATACLASSES                                                  ║
# ╚══════════════════════════════════════════════════════════════╝

@dataclass
class Aposta:
    """Estado de UMA estratégia (HT/PA/PB) num jogo."""
    linha_alvo: float | None = None
    wrN: float | None = None
    wr5: float | None = None
    n_dados: int = 0
    init_erro: str | None = None

    entradas: int = 0
    ult_linha_apostada: float = 999.0
    apostou_em: list = field(default_factory=list)

    falhas_seguidas: int = 0
    ult_tentativa: float = 0.0
    bloqueado: bool = False

    def ativa(self) -> bool:
        return self.linha_alvo is not None and not self.bloqueado


@dataclass
class JogoState:
    event_id: int
    nome: str
    champ: str
    liga_key: str
    nick_a: str
    nick_b: str
    id_a: int = 0
    id_b: int = 0

    ht: Aposta = field(default_factory=Aposta)  # Over HT
    pa: Aposta = field(default_factory=Aposta)  # Over Jogador A
    pb: Aposta = field(default_factory=Aposta)  # Over Jogador B

    def get(self, k: str) -> Aposta:
        return getattr(self, k)


@dataclass
class Stats:
    tentativas: int = 0
    aceitas: int = 0
    rejeitadas: int = 0
    por_error_type: Counter = field(default_factory=Counter)
    por_estrat_tent: Counter = field(default_factory=Counter)
    por_estrat_ok:   Counter = field(default_factory=Counter)

    def conv_pct(self) -> float:
        return (self.aceitas / self.tentativas * 100) if self.tentativas else 0.0

    def linha(self) -> str:
        return (f"tent={self.tentativas} ok={self.aceitas} "
                f"rej={self.rejeitadas} conv={self.conv_pct():.1f}%")


STATS = Stats()


def csv_init():
    if LOG_CSV.exists():
        return
    with LOG_CSV.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow([
            "ts", "event_id", "evento", "liga", "estrategia",
            "linha_alvo", "linha_live", "odd", "stake",
            "ok", "error_type", "ticket_id", "entrada_n",
        ])


def csv_log(ev, j, estrat, ap, linha_live, odd, stake, resp):
    try:
        with LOG_CSV.open("a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow([
                datetime.now(timezone.utc).isoformat(),
                ev["id"], ev["name"], j.liga_key, estrat,
                ap.linha_alvo, linha_live, odd, stake,
                int(bool(resp.get("ok"))),
                resp.get("error_type", ""),
                resp.get("ticket_id", "") or "",
                ap.entradas + (1 if resp.get("ok") else 0),
            ])
    except Exception as e:
        print(f"   ⚠️ csv erro: {e}")


# ╔══════════════════════════════════════════════════════════════╗
# ║  TIPMANAGER                                                   ║
# ╚══════════════════════════════════════════════════════════════╝

def tm_login() -> str:
    r = requests.post(
        f"{SUPA_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": SUPA_KEY, "Content-Type": "application/json"},
        json={"email": TM_EMAIL, "password": TM_SENHA}, timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def tm_fetch_players(t_id: int) -> dict[str, int]:
    url = (f"https://api.tipmanager.net/v1/players"
           f"?id_sport=2&id_tournament={t_id}&place=9")
    r = requests.get(url, headers=TM_HEADERS_PLAYERS, timeout=15)
    r.raise_for_status()
    return {p["description"].lower().strip(): p["id"] for p in r.json()}


def tm_decrypt(raw: bytes) -> dict:
    iv, ct, tag = raw[:12], raw[12:-16], raw[-16:]
    cipher = AES.new(TM_AES_KEY, AES.MODE_GCM, nonce=iv, mac_len=16)
    plain = cipher.decrypt_and_verify(ct, tag)
    return json.loads(gzip.decompress(plain))


def tm_fetch_h2h(supa_token: str, id_a: int, id_b: int) -> list:
    """Retorna last_50 do H2H (lista de jogos, mais recente primeiro)."""
    body = {"id_sport": 2, "id_player_a": id_a, "id_player_b": id_b,
            "timezone": "America/Sao_Paulo", "hour_range": [0, 24]}
    headers = {
        "Authorization": f"Bearer {TM_APP_TOKEN}",
        "x-api-key": supa_token,
        "Content-Type": "application/json",
        "Origin": "https://tipmanager.net",
        "Referer": "https://tipmanager.net/",
    }
    for tent in range(3):
        try:
            r = cfr.post(TM_API_H2H, json=body, headers=headers,
                         impersonate="chrome110", timeout=25, verify=False)
            if r.status_code == 200:
                d = tm_decrypt(r.content)
                return d.get("info", {}).get("last_50", []) if isinstance(d, dict) else []
            if r.status_code == 429:
                time.sleep(3)
                continue
            return []
        except Exception:
            time.sleep(1)
    return []


def extrair_scores_ht(jogos: list) -> list[int]:
    """soma score_ht_a + score_ht_b de cada jogo do H2H."""
    out = []
    for g in jogos:
        if not isinstance(g, dict):
            continue
        sh = g.get("scores_ht") or {}
        sa, sb = sh.get("score_ht_a"), sh.get("score_ht_b")
        if sa is None or sb is None:
            continue
        try:
            out.append(int(sa) + int(sb))
        except (TypeError, ValueError):
            pass
    return out


def extrair_scores_ft_player(jogos: list, player_id: int) -> list[int]:
    """Extrai pontuação FT do PLAYER específico no H2H.
    O player_a/b varia por jogo, então identifica pelo id_player_a/b."""
    out = []
    for g in jogos:
        if not isinstance(g, dict):
            continue
        sf = g.get("scores_ft") or {}
        if g.get("id_player_a") == player_id:
            sc = sf.get("score_ft_a")
        elif g.get("id_player_b") == player_id:
            sc = sf.get("score_ft_b")
        else:
            continue
        if sc is None:
            continue
        try:
            out.append(int(sc))
        except (TypeError, ValueError):
            pass
    return out


def melhor_linha(scores: list[int], n_window: int, wrN_min: float,
                 wr5_min: float, linha_max: float = 999.0):
    """Retorna (linha, wrN, wr5) — MAIOR linha que atende ambos critérios.
    Limita pela linha_max. None se nada atende."""
    if len(scores) < n_window or len(scores) < 5:
        return None
    uN = scores[:n_window]
    u5 = scores[:5]
    melhor = None
    li = 20  # começa em 10.0
    while True:
        ln = li * 0.5
        if ln > linha_max:
            break
        if ln > 200:  # safety
            break
        wrN = sum(1 for s in uN if s > ln) / n_window
        wr5 = sum(1 for s in u5 if s > ln) / 5
        if wrN >= wrN_min and wr5 >= wr5_min:
            melhor = (ln, wrN, wr5)
        li += 1
    return melhor


# ╔══════════════════════════════════════════════════════════════╗
# ║  VUPI AUTH                                                    ║
# ╚══════════════════════════════════════════════════════════════╝

async def vp_capturar_auth(page) -> tuple[str, str]:
    keys = await page.evaluate("() => Object.keys(localStorage)")
    print(f"  localStorage keys: {keys}")
    state = {"sid": None, "idt": None}
    for k in keys:
        if k.lower() in ("sessionid", "session_id"):
            state["sid"] = await page.evaluate(
                f"() => localStorage.getItem('{k}')")
        if k.lower() in ("identity", "identity_token", "user_identity"):
            state["idt"] = await page.evaluate(
                f"() => localStorage.getItem('{k}')")
    if state["sid"] and state["idt"]:
        return state["sid"], state["idt"]

    def on_req(req):
        h = req.headers
        if not state["sid"] and h.get("sessionid"):
            state["sid"] = h["sessionid"]
        if not state["idt"] and h.get("identity"):
            state["idt"] = h["identity"]

    page.on("request", on_req)
    print("  reloading pra capturar headers...")
    await page.reload(wait_until="domcontentloaded")
    for _ in range(40):
        if state["sid"] and state["idt"]:
            return state["sid"], state["idt"]
        await asyncio.sleep(0.5)
    raise RuntimeError(f"falha capturar auth")


async def vp_obter_jwt(ctx, sessionid: str, identity: str) -> tuple[str, float]:
    r1 = await ctx.request.get(
        f"{VP_BFF}/sports/openSportsBook?vendorId=altenar",
        headers={"sessionid": sessionid, "identity": identity,
                 "Accept": "application/json"})
    body1_text = await r1.text()
    if r1.status != 200:
        raise RuntimeError(f"openSportsBook HTTP {r1.status}: {body1_text[:300]}")
    body1 = json.loads(body1_text)
    auth_token = body1.get("authToken") or body1.get("data", {}).get("authToken")
    if not auth_token:
        raise RuntimeError(f"sem authToken: {body1}")

    r2 = await ctx.request.post(
        f"{VP_AUTH}/api/Auth/SignIn",
        data=json.dumps({"integration": "vupi", "token": auth_token}),
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    text = await r2.text()
    if r2.status != 200:
        raise RuntimeError(f"SignIn HTTP {r2.status}: {text[:200]}")
    d2 = json.loads(text)
    jwt = d2.get("Result", {}).get("AccessToken") or d2.get("accessToken")
    if not jwt:
        raise RuntimeError(f"sem JWT: {text[:200]}")

    exp = time.time() + 3600
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        d = json.loads(base64.urlsafe_b64decode(payload))
        exp = float(d.get("exp", exp))
    except Exception:
        pass
    return jwt, exp


# ╔══════════════════════════════════════════════════════════════╗
# ║  VUPI SPORTSBOOK                                              ║
# ╚══════════════════════════════════════════════════════════════╝

async def vp_listagem(client: httpx.AsyncClient) -> list[dict]:
    p = {**VP_PARAMS, "sportIds": 147, "eventCount": 0}
    r = await client.get(f"{VP_FRONT}/api/Sportsbook/GetLiveEvents",
                         params=p, headers=VP_HEADERS)
    r.raise_for_status()
    data = r.json()
    out = []
    for sport in data.get("Result", {}).get("Items", []) or []:
        for champ in sport.get("Items", []) or []:
            for ev in champ.get("Events", []) or []:
                out.append({
                    "id": ev["Id"],
                    "name": ev.get("Name", ""),
                    "champ": ev.get("ChampName", ""),
                    "category": ev.get("CategoryName", ""),
                    "live_time": ev.get("LiveCurrentTime", ""),
                    "score": ev.get("LiveScore", ""),
                    "raw": ev,
                })
    return out


async def vp_detalhe(client: httpx.AsyncClient, eid: int) -> dict | None:
    p = {**VP_PARAMS, "eventId": eid}
    try:
        r = await client.get(f"{VP_FRONT}/api/Sportsbook/GetEventDetails",
                             params=p, headers=VP_HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json().get("Result", {})
    except Exception as e:
        print(f"   detalhe {eid} erro: {e}")
    return None


def encontrar_mercado(detalhe: dict, market_type_id: int) -> dict | None:
    for mg in detalhe.get("MarketGroups", []) or []:
        for mk in mg.get("Markets") or mg.get("Items") or []:
            mt = mk.get("OrgMarketTypeId") or mk.get("MarketTypeId")
            if mt == market_type_id or str(mt).rstrip("_") == str(market_type_id):
                return mk
    return None


def encontrar_selecao_over(market: dict) -> dict | None:
    for s in market.get("Selections") or market.get("Items") or market.get("Odds") or []:
        if s.get("SelectionTypeId") == 12:
            return s
    return None


def parse_linha(spov: str) -> float | None:
    if not spov:
        return None
    try:
        return float(str(spov).split("|")[-1])
    except (ValueError, AttributeError):
        return None


def nanoid(n: int = 21) -> str:
    alpha = string.ascii_letters + string.digits + "_-"
    return "".join(random.choices(alpha, k=n))


_APOSTA_DUMP_FEITO = False


def _find_ticket_id(d):
    paths = [
        ("Result", "TicketId"), ("Result", "ticketId"),
        ("Result", "Id"), ("Result", "id"),
        ("ticketId",), ("TicketId",), ("Id",), ("id",),
        ("data", "ticketId"), ("data", "TicketId"),
        ("Result", "Ticket", "Id"), ("Result", "BetSlip", "Id"),
    ]
    for path in paths:
        cur = d
        try:
            for k in path:
                cur = cur[k]
            if cur:
                return cur
        except (KeyError, TypeError):
            continue
    return None


async def vp_apostar(ctx, jwt, ev, market, sel, stake) -> dict:
    global _APOSTA_DUMP_FEITO
    raw_ev = ev["raw"]
    body = {
        "culture": "pt-BR", "timezoneOffset": 180, "integration": "vupi",
        "deviceType": 1, "numFormat": "en-GB", "countryCode": "BR",
        "betType": 0, "isAutoCharge": False,
        "stakes": [stake],
        "oddsChangeAction": 2 if ACEITA_MUDANCA_ODD else 0,
        "betMarkets": [{
            "id": ev["id"],
            "isBanker": False,
            "dbId": raw_ev.get("DbId", 10),
            "sportName": raw_ev.get("SportName", "E-Basquete"),
            "sportTypeId": raw_ev.get("SportTypeId", 320),
            "rC": False,
            "eventName": ev["name"],
            "catName": raw_ev.get("CategoryName", ""),
            "champName": raw_ev.get("ChampName", ""),
            "odds": [{
                "id": sel["Id"],
                "sPOV": sel.get("SPOV", ""),
                "marketId": market["Id"],
                "price": sel["Price"],
                "marketName": market.get("Name", ""),
                "marketTypeId": market.get("OrgMarketTypeId") or market.get("MarketTypeId"),
                "mostBalanced": sel.get("MB", 0) == 1,
                "selectionTypeId": sel.get("SelectionTypeId", 12),
                "selectionName": sel.get("Name", ""),
                "widgetInfo": {"widget": 12, "page": 4, "tabIndex": 4,
                               "tipsterId": None, "suggestionType": None}
            }]
        }],
        "eachWays": [False],
        "requestId": nanoid(21),
        "confirmedByClient": False,
        "device": 0,
    }
    try:
        r = await ctx.request.post(
            f"{VP_BET}/api/widget/placeWidget",
            data=json.dumps(body),
            headers={"Authorization": f"Bearer {jwt}",
                     "Content-Type": "application/json"})
        text = await r.text()
        try:
            d = json.loads(text)
        except json.JSONDecodeError:
            return {"ok": False, "error_type": "non_json",
                    "raw": text[:500], "status": r.status}
        if not _APOSTA_DUMP_FEITO:
            try:
                Path("vupi_aposta_response.json").write_text(
                    json.dumps(d, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                print(f"   1ª aposta dump -> vupi_aposta_response.json")
                _APOSTA_DUMP_FEITO = True
            except Exception:
                pass
        if r.status == 200 and "errorType" not in d:
            return {"ok": True, "ticket_id": _find_ticket_id(d), "raw": d}
        return {"ok": False, "error_type": d.get("errorType"),
                "raw": d, "status": r.status}
    except Exception as e:
        return {"ok": False, "error_type": "exception", "raw": str(e)}


# ╔══════════════════════════════════════════════════════════════╗
# ║  HELPERS                                                      ║
# ╚══════════════════════════════════════════════════════════════╝

def extrair_nicks(name: str):
    nicks = re.findall(r"\(([^)]+)\)", name or "")
    return (nicks[0].strip(), nicks[1].strip()) if len(nicks) >= 2 else None


def mapear_liga(champ_name: str):
    cn = (champ_name or "").lower()
    if "conference" in cn:
        return "conference"
    if "esportsbattle" in cn or "battle" in cn:
        return "battle"
    return None


def setar_aposta(ap: Aposta, scores: list, cfg: dict):
    """Calcula linha alvo dessa aposta com base nos scores e configs."""
    ap.n_dados = len(scores)
    if len(scores) < cfg["n"]:
        ap.init_erro = f"dados<{cfg['n']} (tem {len(scores)})"
        return
    res = melhor_linha(scores, cfg["n"], cfg["wrN_min"],
                       cfg["wr5_min"], cfg["linha_max"])
    if not res:
        ap.init_erro = f"nenhuma linha <={cfg['linha_max']} atende"
        return
    ap.linha_alvo, ap.wrN, ap.wr5 = res


def inicializar_jogo(j: JogoState, supa_token: str,
                     players_cache: dict[int, dict]):
    """Calcula linha alvo das 3 estratégias (HT, PA, PB) usando o H2H."""
    t_id = 5  # battle e conference no torneio 5
    if t_id not in players_cache:
        try:
            players_cache[t_id] = tm_fetch_players(t_id)
            print(f"  [TM] cache torneio {t_id}: "
                  f"{len(players_cache[t_id])} players")
        except Exception as e:
            for k in ("ht", "pa", "pb"):
                j.get(k).init_erro = f"tm players: {e}"
            return

    pmap = players_cache[t_id]
    j.id_a = pmap.get(j.nick_a.lower(), 0)
    j.id_b = pmap.get(j.nick_b.lower(), 0)
    if not j.id_a or not j.id_b:
        for k in ("ht", "pa", "pb"):
            j.get(k).init_erro = (f"nicks nao achados: "
                                  f"{j.nick_a}={j.id_a} {j.nick_b}={j.id_b}")
        return

    h2h = tm_fetch_h2h(supa_token, j.id_a, j.id_b)
    if not h2h:
        for k in ("ht", "pa", "pb"):
            j.get(k).init_erro = "h2h vazio"
        return

    # Pré-calcula scores
    scores_ht = extrair_scores_ht(h2h)
    scores_pa = extrair_scores_ft_player(h2h, j.id_a)
    scores_pb = extrair_scores_ft_player(h2h, j.id_b)

    # ─── HT ───
    cfg = ESTRATEGIAS["ht"]["ligas"].get(j.liga_key)
    if cfg:
        setar_aposta(j.ht, scores_ht, cfg)
    else:
        j.ht.init_erro = f"sem config pra {j.liga_key}"

    # ─── PA ───
    cfg = ESTRATEGIAS["pa"]["ligas"].get(j.liga_key)
    if cfg:
        setar_aposta(j.pa, scores_pa, cfg)
    else:
        j.pa.init_erro = f"sem config pra {j.liga_key}"

    # ─── PB ───
    cfg = ESTRATEGIAS["pb"]["ligas"].get(j.liga_key)
    if cfg:
        setar_aposta(j.pb, scores_pb, cfg)
    else:
        j.pb.init_erro = f"sem config pra {j.liga_key}"


# ╔══════════════════════════════════════════════════════════════╗
# ║  PROCESSAR 1 ESTRATÉGIA NO LOOP                              ║
# ╚══════════════════════════════════════════════════════════════╝

async def processar_estrategia(ctx, jwt, httpx_client, ev, j: JogoState,
                               estrat_key: str, detalhe_cache: dict):
    estrat_def = ESTRATEGIAS[estrat_key]
    cfg = estrat_def["ligas"].get(j.liga_key)
    if not cfg:
        return False  # sem config

    ap = j.get(estrat_key)
    if not ap.ativa():
        return False
    if ap.entradas >= cfg["max_ent"]:
        return False
    now = time.time()
    if now - ap.ult_tentativa < COOLDOWN_TENTATIVA:
        return False
    if not any(t.lower() in ev["live_time"].lower()
               for t in estrat_def["tempos_ok"]):
        return False

    # cache de detalhe pra não chamar 3x no mesmo ciclo
    if ev["id"] not in detalhe_cache:
        detalhe_cache[ev["id"]] = await vp_detalhe(httpx_client, ev["id"])
    det = detalhe_cache[ev["id"]]
    if not det:
        return True

    mk = encontrar_mercado(det, estrat_def["market_type_id"])
    if not mk or mk.get("Status") != 1:
        return True
    sel = encontrar_selecao_over(mk)
    if not sel or not sel.get("IsActive"):
        return True
    linha_live = parse_linha(sel.get("SPOV"))
    if linha_live is None:
        return True

    # decisão
    if linha_live <= ap.linha_alvo and linha_live < ap.ult_linha_apostada:
        print(f"\n🎯 [{estrat_key.upper()}] {ev['name']}")
        print(f"   live: Over {linha_live} @ {sel['Price']}  "
              f"alvo<={ap.linha_alvo}  ult={ap.ult_linha_apostada}")
        ap.ult_tentativa = time.time()
        resp = await vp_apostar(ctx, jwt, ev, mk, sel, cfg["stake"])

        STATS.tentativas += 1
        STATS.por_estrat_tent[f"{estrat_key}/{j.liga_key}"] += 1
        if resp["ok"]:
            STATS.aceitas += 1
            STATS.por_estrat_ok[f"{estrat_key}/{j.liga_key}"] += 1
        else:
            STATS.rejeitadas += 1
            STATS.por_error_type[str(resp.get("error_type"))] += 1

        csv_log(ev, j, estrat_key, ap, linha_live, sel["Price"],
                cfg["stake"], resp)

        if resp["ok"]:
            ap.entradas += 1
            ap.ult_linha_apostada = linha_live
            ap.falhas_seguidas = 0
            ap.apostou_em.append((linha_live, resp["ticket_id"]))
            print(f"   ✅ APOSTOU R${cfg['stake']}  "
                  f"ticket={resp['ticket_id']}  "
                  f"({ap.entradas}/{cfg['max_ent']})  [{STATS.linha()}]")
        else:
            et = resp.get("error_type")
            if et not in ERROS_IGNORAR_BLOQUEIO:
                ap.falhas_seguidas += 1
            print(f"   ❌ falhou ({ap.falhas_seguidas}/{MAX_FALHAS_SEGUIDAS}): "
                  f"errorType={et}  status={resp.get('status')}  "
                  f"[{STATS.linha()}]")
            if ap.falhas_seguidas >= MAX_FALHAS_SEGUIDAS:
                ap.bloqueado = True
                print(f"   🚫 [{estrat_key.upper()}] BLOQUEADO")
    return True


# ╔══════════════════════════════════════════════════════════════╗
# ║  MAIN                                                         ║
# ╚══════════════════════════════════════════════════════════════╝

async def main():
    print("=" * 62)
    print(" BOT VUPI — eBasket multi-estrat (HT + PlayerA + PlayerB)")
    print("=" * 62)
    for ek, ed in ESTRATEGIAS.items():
        ligas = list(ed["ligas"].keys())
        print(f"  [{ek.upper()}] {ed['tipo']}  mt={ed['market_type_id']}  "
              f"ligas={ligas}")
    print()

    pw = await async_playwright().start()
    print(f"-> conectando Brave CDP :{CDP_PORT}...")
    browser = await pw.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
    if not browser.contexts:
        print("X Brave sem contexts")
        return
    ctx = browser.contexts[0]
    page = None
    for p in ctx.pages:
        if "vupi.bet.br" in p.url:
            page = p
            break
    if not page:
        page = ctx.pages[0]
        await page.goto("https://www.vupi.bet.br/", wait_until="domcontentloaded")
    print(f"  aba: {page.url}")

    print("-> auth Vupi...")
    sessionid, identity = await vp_capturar_auth(page)
    print(f"  sessionid={sessionid[:20]}...  identity={identity[:20]}...")

    print("-> JWT Altenar...")
    jwt, jwt_exp = await vp_obter_jwt(ctx, sessionid, identity)
    print(f"  exp em {(jwt_exp - time.time()):.0f}s")

    print("-> login TipManager...")
    supa_token = tm_login()
    supa_login_at = time.time()

    csv_init()
    print(f"-> log CSV: {LOG_CSV.resolve()}")

    players_cache: dict[int, dict] = {}
    jogos: dict[int, JogoState] = {}
    ciclo = 0

    print("\n" + "-" * 62)
    print("LOOP iniciado (Ctrl+C pra parar)\n")

    httpx_client = httpx.AsyncClient(timeout=10)

    try:
        while True:
            ciclo += 1
            now = time.time()

            if now >= jwt_exp - 120:
                try:
                    jwt, jwt_exp = await vp_obter_jwt(ctx, sessionid, identity)
                    print(f"[{ciclo}] JWT renovado, exp +{(jwt_exp-now):.0f}s")
                except Exception as e:
                    print(f"[{ciclo}] erro renovando JWT: {e}")

            if now - supa_login_at > 50 * 60:
                try:
                    supa_token = tm_login()
                    supa_login_at = now
                    print(f"[{ciclo}] Supabase relogado")
                except Exception as e:
                    print(f"[{ciclo}] erro Supabase: {e}")

            try:
                eventos = await vp_listagem(httpx_client)
            except Exception as e:
                print(f"[{ciclo}] listagem erro: {e}")
                await asyncio.sleep(SLEEP_LOOP)
                continue

            ativos = 0
            detalhe_cache: dict[int, dict] = {}

            for ev in eventos:
                liga = mapear_liga(ev["champ"])
                if not liga:
                    continue

                # detecta jogo NOVO
                if ev["id"] not in jogos:
                    nicks = extrair_nicks(ev["name"])
                    if not nicks:
                        continue
                    j = JogoState(
                        event_id=ev["id"], nome=ev["name"],
                        champ=ev["champ"], liga_key=liga,
                        nick_a=nicks[0], nick_b=nicks[1],
                    )
                    inicializar_jogo(j, supa_token, players_cache)
                    jogos[ev["id"]] = j

                    print(f"\n🆕 [{liga.upper()}] {ev['name']}")
                    for k in ("ht", "pa", "pb"):
                        ap = j.get(k)
                        if ap.linha_alvo:
                            print(f"   [{k}] alvo Over {ap.linha_alvo}  "
                                  f"WR{ESTRATEGIAS[k]['ligas'][liga]['n']}="
                                  f"{ap.wrN:.0%}  WR5={ap.wr5:.0%}  "
                                  f"(n={ap.n_dados})")
                        elif ap.init_erro:
                            print(f"   [{k}] -- {ap.init_erro}")

                j = jogos[ev["id"]]
                # processa as 3 estratégias
                for k in ("ht", "pa", "pb"):
                    if await processar_estrategia(ctx, jwt, httpx_client, ev,
                                                  j, k, detalhe_cache):
                        ativos += 1

            if ciclo % 10 == 0:
                rast = sum(1 for j in jogos.values()
                           if any(j.get(k).linha_alvo for k in ("ht","pa","pb")))
                print(f"[{ciclo}]  evs={len(eventos)}  "
                      f"rastreados={rast}  ativos={ativos}  "
                      f"{STATS.linha()}")

            await asyncio.sleep(SLEEP_LOOP)

    except KeyboardInterrupt:
        print("\n\n🛑 PARADO")
    finally:
        await httpx_client.aclose()
        await pw.stop()

        print("\n" + "=" * 62)
        print("RESUMO FINAL")
        print("=" * 62)
        print(f"  Tentativas:   {STATS.tentativas}")
        print(f"  Aceitas:      {STATS.aceitas}")
        print(f"  Rejeitadas:   {STATS.rejeitadas}")
        print(f"  Conversão:    {STATS.conv_pct():.1f}%")

        if STATS.por_estrat_tent:
            print(f"\n  Por estratégia/liga:")
            for chave in sorted(STATS.por_estrat_tent):
                t = STATS.por_estrat_tent[chave]
                ok = STATS.por_estrat_ok.get(chave, 0)
                conv = (ok / t * 100) if t else 0
                print(f"    {chave:20s}  tent={t:4d}  ok={ok:4d}  "
                      f"conv={conv:.1f}%")

        if STATS.por_error_type:
            print(f"\n  Erros por tipo:")
            for et, n in STATS.por_error_type.most_common():
                print(f"    {et}: {n}")

        print(f"\n  Jogos com aposta:")
        for j in jogos.values():
            if any(j.get(k).entradas for k in ("ht","pa","pb")):
                print(f"    {j.nome}  ({j.liga_key})")
                for k in ("ht", "pa", "pb"):
                    ap = j.get(k)
                    if ap.entradas:
                        print(f"      [{k}] alvo={ap.linha_alvo}  "
                              f"entradas={ap.entradas}")
                        for ln, tk in ap.apostou_em:
                            print(f"        • Over {ln}  ticket={tk}")

        print(f"\n  Log completo: {LOG_CSV.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())