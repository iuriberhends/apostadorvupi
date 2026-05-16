"""
vupi_erivals.py — Vupi eFootball FC26: Over HT com filtro score-based.

FILTRO (3 regras AND):
  1) score_home >= score_away (casa ganhando ou empate)
  2) score_home + score_away >= 1 (NUNCA aposta em 0x0)
  3) 1 <= (linha + 0.5) - total <= 2 (over HT a 1 ou 2 gols de bater)

Mercado: Over HT (busca pelo nome do mercado, não pelo ID).
Liga: qualquer ChampName contendo "FC26".

USO:
    python vupi_erivals.py
"""

import asyncio
import csv
import json
import re
import string
import os
import time
import random
import base64
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from playwright.async_api import async_playwright


# ╔══════════════════════════════════════════════════════════════╗
# ║  CONFIG  — ajustar conforme necessidade                       ║
# ╚══════════════════════════════════════════════════════════════╝

CDP_PORT = 9231

# TODO: mover para .env (ex: pip install python-dotenv; load_dotenv())
EMAIL_VUPI = os.getenv("VUPI_EMAIL", "msdj730@gmail.com")
SENHA_VUPI = os.getenv("VUPI_SENHA", "Haniel123")

# Loop
SLEEP_LOOP         = 0.5
ACEITA_MUDANCA_ODD = True   # oddsChangeAction=3 → Vupi aceita qualquer mudança

# Liga
LIGA_KEYWORD = "h2h gg"   # case-insensitive, ignora espaços ("FC 26" também passa)

# Aposta — AJUSTAR
STAKE = 1.0
MAX_APOSTAS_POR_JOGO = 2

# Erros
COOLDOWN_TENTATIVA      = 1
MAX_FALHAS_SEGUIDAS     = 9999
ERROS_IGNORAR_BLOQUEIO  = {4, "4"}  # errorType=4 = linha mudou (race normal)

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

LOG_CSV = Path("apostas_log_erivals.csv")


# ╔══════════════════════════════════════════════════════════════╗
# ║  DATACLASSES                                                  ║
# ╚══════════════════════════════════════════════════════════════╝

@dataclass
class JogoState:
    event_id: int
    nome: str
    champ: str
    entradas: int = 0
    ult_linha_apostada: float = 999.0
    falhas_seguidas: int = 0
    ult_tentativa: float = 0.0
    bloqueado: bool = False
    apostou_em: list = field(default_factory=list)
    # debug
    market_type_id_visto: int | None = None


@dataclass
class Stats:
    tentativas: int = 0
    aceitas: int = 0
    rejeitadas: int = 0
    por_error_type: Counter = field(default_factory=Counter)
    motivos_filtro: Counter = field(default_factory=Counter)

    def conv_pct(self) -> float:
        return (self.aceitas / self.tentativas * 100) if self.tentativas else 0.0


STATS = Stats()


def csv_init():
    if LOG_CSV.exists():
        return
    with LOG_CSV.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow([
            "ts", "event_id", "evento", "liga", "score", "linha",
            "gols_pra_bater", "odd", "stake", "ok", "error_type",
            "ticket_id", "entrada_n", "mt_id",
        ])


def csv_log(ev, j, score, linha, gols_pb, odd, stake, mt_id, resp):
    try:
        with LOG_CSV.open("a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow([
                datetime.now(timezone.utc).isoformat(),
                ev["id"], ev["name"], ev.get("champ", ""), score,
                linha, gols_pb, odd, stake,
                int(bool(resp.get("ok"))),
                resp.get("error_type", ""),
                resp.get("ticket_id", "") or "",
                j.entradas + (1 if resp.get("ok") else 0),
                mt_id,
            ])
    except Exception as e:
        print(f"   ⚠️ csv erro: {e}")


# ╔══════════════════════════════════════════════════════════════╗
# ║  VUPI AUTH                                                    ║
# ╚══════════════════════════════════════════════════════════════╝

async def vp_capturar_auth(page) -> tuple[str, str]:
    keys = await page.evaluate("() => Object.keys(localStorage)")
    print(f"  localStorage keys: {keys}")
    state = {"sid": None, "idt": None}
    for k in keys:
        if k.lower() in ("sessionid", "session_id"):
            state["sid"] = await page.evaluate(f"() => localStorage.getItem('{k}')")
        if k.lower() in ("identity", "identity_token", "user_identity"):
            state["idt"] = await page.evaluate(f"() => localStorage.getItem('{k}')")
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
    raise RuntimeError("falha capturar auth")


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

    # Body completo conforme captura real (precisa de todos os VP_PARAMS + token)
    signin_body = {**VP_PARAMS, "token": auth_token}
    r2 = await ctx.request.post(
        f"{VP_AUTH}/api/WidgetAuth/SignIn",
        data=json.dumps(signin_body),
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    text = await r2.text()
    if r2.status != 200:
        raise RuntimeError(f"SignIn HTTP {r2.status}: {text[:200]}")
    d2 = json.loads(text)
    # Resposta nova vem no root, não em Result (mantém fallback)
    jwt = d2.get("accessToken") or d2.get("Result", {}).get("AccessToken")
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
# ║  VUPI LOGIN AUTOMÁTICO                                        ║
# ╚══════════════════════════════════════════════════════════════╝

async def vp_esta_logado(page) -> bool:
    """Verifica login via DOM — saldo visível = logado, botão Entrar visível = não logado.
    Mais confiável que storage (Vupi pode usar HttpOnly cookies)."""
    try:
        result = await page.evaluate("""
            () => {
                // 1. Procura saldo/balance visível
                const balanceSelectors = [
                    '[data-testid*="balance" i]',
                    '[data-testid*="saldo" i]',
                    '[data-testid="header-balance-value"]',
                    '[class*="balance" i]',
                    '[class*="Saldo" i]',
                    '[class*="HeaderBalance" i]',
                    '[class*="userBalance" i]',
                ];
                for (const sel of balanceSelectors) {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        const rect = el.getBoundingClientRect();
                        const visible = rect.width > 0 && rect.height > 0;
                        const text = (el.innerText || '').trim();
                        if (visible && text && /R\\$|\\d/.test(text)) {
                            return { logged: true, via: 'balance', value: text.slice(0, 40), sel };
                        }
                    }
                }

                // 2. Procura botão "Entrar" — se visível = NÃO logado
                let entrarVisible = false;
                const candidates = document.querySelectorAll('button, a');
                for (const el of candidates) {
                    const t = (el.innerText || '').trim().toLowerCase();
                    if (t === 'entrar' || t === 'login' || t === 'entrar / cadastrar') {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {
                            entrarVisible = true;
                            break;
                        }
                    }
                }
                if (entrarVisible) {
                    return { logged: false, via: 'entrar_button_visible' };
                }

                // 3. Procura elemento de profile/avatar/menu user (indicador secundário)
                const userIndicators = [
                    '[data-testid*="user-menu" i]',
                    '[data-testid*="profile" i]',
                    '[data-testid*="account" i]',
                    '[class*="userMenu" i]',
                    '[class*="UserMenu" i]',
                    '[class*="Avatar" i]',
                ];
                for (const sel of userIndicators) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {
                            return { logged: true, via: 'user_menu', sel };
                        }
                    }
                }

                // Inconclusivo — sem saldo visível, sem botão Entrar, sem menu user
                return { logged: false, via: 'inconclusive' };
            }
        """)
        if result and result.get("logged"):
            print(f"  ✓ logado via {result.get('via')}"
                  + (f" ({result.get('value')})" if result.get('value') else ""))
            return True
        else:
            via = result.get("via", "?") if result else "erro"
            print(f"  ✗ não logado (via: {via})")
            return False
    except Exception as e:
        print(f"  erro vp_esta_logado: {e}")
        return False


async def vp_dump_storage(page):
    """Imprime keys de localStorage e sessionStorage pra debug."""
    try:
        debug = await page.evaluate("""
            () => ({
                ls: Object.fromEntries(
                    Object.keys(localStorage).map(k => [k, (localStorage.getItem(k) || '').slice(0, 60)])
                ),
                ss: Object.fromEntries(
                    Object.keys(sessionStorage).map(k => [k, (sessionStorage.getItem(k) || '').slice(0, 60)])
                ),
                cookies: document.cookie ? document.cookie.split('; ').map(c => c.split('=')[0]) : [],
            })
        """)
        print(f"    DEBUG localStorage keys: {list(debug['ls'].keys())}")
        for k, v in debug['ls'].items():
            print(f"      ls[{k}] = {v!r}")
        print(f"    DEBUG sessionStorage keys: {list(debug['ss'].keys())}")
        for k, v in debug['ss'].items():
            print(f"      ss[{k}] = {v!r}")
        print(f"    DEBUG cookie names: {debug['cookies']}")
    except Exception as e:
        print(f"    debug erro: {e}")


async def vp_fazer_login(page) -> bool:
    """Login automático na Vupi. Seletores defensivos (várias variações)."""
    print("  🔐 fazendo login automático na Vupi...")

    if "vupi.bet.br" not in (page.url or ""):
        await page.goto("https://www.vupi.bet.br/",
                        wait_until="domcontentloaded")
        await asyncio.sleep(2)

    # 1. Abre modal de login
    abrir_login_selectors = [
        'button:has-text("Entrar")',
        'a:has-text("Entrar")',
        'button:has-text("Login")',
        '[data-testid*="login"][role="button"]',
        'header button:has-text("Entrar")',
    ]
    for sel in abrir_login_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                await btn.click()
                print(f"    modal aberto via: {sel}")
                await asyncio.sleep(1.5)
                break
        except Exception:
            continue

    # 2. Campo email/CPF
    email_selectors = [
        'input[data-testid="login-input-cpf-email"]',
        'input[data-testid*="email"]',
        'input[data-testid*="cpf"]',
        'input[name="username"]',
        'input[name="email"]',
        'input[name="cpf"]',
        'input[type="email"]',
        'input[placeholder*="mail" i]',
        'input[placeholder*="cpf" i]',
        'input[autocomplete="username"]',
    ]
    email_field = None
    for sel in email_selectors:
        try:
            f = page.locator(sel).first
            if await f.is_visible(timeout=1500):
                email_field = f
                print(f"    campo email: {sel}")
                break
        except Exception:
            continue
    if not email_field:
        print("    ❌ campo de email não encontrado")
        return False
    await email_field.click()
    await email_field.fill("")
    await email_field.type(EMAIL_VUPI, delay=random.randint(30, 80))

    # 3. Campo senha
    senha_selectors = [
        'input[data-testid="login-input-password"]',
        'input[type="password"]',
        'input[data-testid*="password"]',
        'input[name="password"]',
        'input[autocomplete="current-password"]',
    ]
    senha_field = None
    for sel in senha_selectors:
        try:
            f = page.locator(sel).first
            if await f.is_visible(timeout=1500):
                senha_field = f
                print(f"    campo senha: {sel}")
                break
        except Exception:
            continue
    if not senha_field:
        print("    ❌ campo de senha não encontrado")
        return False
    await senha_field.click()
    await senha_field.fill("")
    await senha_field.type(SENHA_VUPI, delay=random.randint(30, 70))
    await asyncio.sleep(0.3)

    # 4. Submete
    submit_selectors = [
        'button[data-testid="login-button-submit"]',
        'button[data-testid*="submit"]',
        'button[type="submit"]',
        'form button:has-text("Entrar")',
    ]
    submitted = False
    for sel in submit_selectors:
        try:
            btn = page.locator(sel).last
            if await btn.is_visible(timeout=1000):
                await btn.click()
                print(f"    submit: {sel}")
                submitted = True
                break
        except Exception:
            continue
    if not submitted:
        await senha_field.press("Enter")
        print("    submit: Enter")

    # 5. Aguarda algum sinal de sessão (mais tolerante: 30s)
    print("    aguardando login completar...")
    for _ in range(60):  # ~30s
        if await vp_esta_logado(page):
            print(f"    ✅ logado")
            await asyncio.sleep(1)
            return True
        await asyncio.sleep(0.5)

    print("    ⚠️ timeout — checagem não confirmou em 30s")
    print("    fazendo dump de storage pra debug:")
    await vp_dump_storage(page)
    return False


# ╔══════════════════════════════════════════════════════════════╗
# ║  VUPI SPORTSBOOK                                              ║
# ╚══════════════════════════════════════════════════════════════╝

async def vp_listagem(client: httpx.AsyncClient) -> list[dict]:
    """Lista TODOS os live events (sem filtro sportId), filtra por LIGA_KEYWORD no champ."""
    p = {**VP_PARAMS, "eventCount": 0}
    r = await client.get(f"{VP_FRONT}/api/Sportsbook/GetLiveEvents",
                         params=p, headers=VP_HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    out = []
    for sport in data.get("Result", {}).get("Items", []) or []:
        for champ in sport.get("Items", []) or []:
            champ_name_top = champ.get("Name", "")
            for ev in champ.get("Events", []) or []:
                # ChampName pode vir no ev ou no champ pai
                champ_name = ev.get("ChampName", "") or champ_name_top
                norm = champ_name.lower().replace(" ", "")
                if LIGA_KEYWORD not in norm:
                    continue
                out.append({
                    "id": ev["Id"],
                    "name": ev.get("Name", ""),
                    "champ": champ_name,
                    "category": ev.get("CategoryName", ""),
                    "live_time": ev.get("LiveCurrentTime", ""),
                    "score": ev.get("LiveScore", ""),
                    "sport_id": sport.get("Id", 0),
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


# Padrões pra reconhecer mercado HT no nome (case-insensitive, vários formatos)
PATTERNS_HT = ("1ª parte", "1ª metade", "1º tempo", "primeiro tempo",
               "1ht", "1 ht", "first half", "halftime", "half-time",
               "half time")
PATTERNS_TOTAL = ("total", "gols", "over/under", "over under", "mais/menos")


def encontrar_mercado_over_ht(detalhe: dict) -> dict | None:
    """Acha o mercado Over HT pelo nome. Retorna o dict do mercado ou None."""
    for mg in detalhe.get("MarketGroups", []) or []:
        for mk in mg.get("Markets") or mg.get("Items") or []:
            nome = (mk.get("Name", "") or "").lower()
            tem_ht = any(p in nome for p in PATTERNS_HT)
            tem_total = any(p in nome for p in PATTERNS_TOTAL)
            if tem_ht and tem_total:
                return mk
    return None


def encontrar_selecao_over(market: dict) -> dict | None:
    for s in market.get("Selections") or market.get("Items") or market.get("Odds") or []:
        if s.get("SelectionTypeId") == 12:  # 12 = Over
            return s
    return None


def parse_linha(spov: str) -> float | None:
    if not spov:
        return None
    try:
        return float(str(spov).split("|")[-1])
    except (ValueError, AttributeError):
        return None


def parse_score_live(score_raw) -> tuple[int, int] | None:
    """Parser flexível pro LiveScore — futebol vem como '1-0', '1:0', '1 - 0', etc.
    Pode vir como string ou lista [h, a]."""
    if score_raw is None:
        return None
    # Lista [h, a]
    if isinstance(score_raw, (list, tuple)) and len(score_raw) >= 2:
        try:
            return (int(score_raw[0]), int(score_raw[1]))
        except (ValueError, TypeError):
            return None
    # String
    s = str(score_raw).strip()
    if not s:
        return None
    # Tenta regex pegando primeiros 2 números
    m = re.match(r"\s*(\d+)\s*[-:xX]\s*(\d+)", s)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


def esta_no_ht(live_time: str) -> bool:
    """True se o jogo está no 1º tempo."""
    if not live_time:
        return False
    lt = live_time.lower()
    PADROES_HT = ("1ª parte", "1ª metade", "1º tempo", "primeiro tempo",
                  "1ht", "1 ht", "1st half", "first half")
    if any(p in lt for p in PADROES_HT):
        return True
    # Padrões de intervalo / 2º tempo / fim
    PADROES_NAO = ("intervalo", "ht ", "ht/", "2º tempo", "2ª parte",
                   "2nd half", "half-time", "halftime", "ft ", "encerrad")
    if any(p in lt for p in PADROES_NAO):
        return False
    # Minutos crus tipo "23'" ou "23"
    m = re.search(r"(\d+)", lt)
    if m:
        try:
            minutos = int(m.group(1))
            return 1 <= minutos <= 45
        except ValueError:
            pass
    return False


def filtro_aprova(sh: int, sa: int, linha: float) -> tuple[bool, float, str]:
    """Aplica filtro do PDF. Retorna (aprovou, gols_pra_bater, motivo)."""
    total = sh + sa
    if sh < sa:
        return False, 0.0, "casa_perdendo"
    if total < 1:
        return False, 0.0, "score_0x0"
    gols_pb = (linha + 0.5) - total
    if not (1 <= gols_pb <= 2):
        return False, gols_pb, f"gols_pb={gols_pb}"
    return True, gols_pb, "OK"


def nanoid(n: int = 21) -> str:
    alpha = string.ascii_letters + string.digits + "_-"
    return "".join(random.choices(alpha, k=n))


_APOSTA_DUMP_FEITO = False


def _find_ticket_id(d):
    paths = [("Result", "TicketId"), ("Result", "ticketId"),
             ("Result", "Id"), ("Result", "id"),
             ("ticketId",), ("TicketId",), ("Id",), ("id",)]
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
            "sportName": raw_ev.get("SportName", ""),
            "sportTypeId": raw_ev.get("SportTypeId", 0),
            "rC": False,
            "eventName": ev["name"],
            "catName": raw_ev.get("CategoryName", ""),
            "champName": raw_ev.get("ChampName", "") or ev["champ"],
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
                "widgetInfo": {"widget": 12, "page": 4, "tabIndex": 3,
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
                Path("vupi_erivals_aposta_response.json").write_text(
                    json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"   1ª aposta dump -> vupi_erivals_aposta_response.json")
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
# ║  PROCESSAR 1 JOGO                                             ║
# ╚══════════════════════════════════════════════════════════════╝

async def processar_jogo(ctx, jwt, httpx_client, ev, j: JogoState):
    if j.bloqueado:
        return
    if j.entradas >= MAX_APOSTAS_POR_JOGO:
        return
    now = time.time()
    if now - j.ult_tentativa < COOLDOWN_TENTATIVA:
        return

    if not esta_no_ht(ev["live_time"]):
        return

    sc = parse_score_live(ev["score"])
    if not sc:
        return
    sh, sa = sc

    det = await vp_detalhe(httpx_client, ev["id"])
    if not det:
        return

    mk = encontrar_mercado_over_ht(det)
    if not mk:
        return
    if mk.get("Status") != 1:
        return
    sel = encontrar_selecao_over(mk)
    if not sel or not sel.get("IsActive"):
        return
    linha = parse_linha(sel.get("SPOV"))
    if linha is None:
        return

    mt_id = mk.get("OrgMarketTypeId") or mk.get("MarketTypeId")
    if not j.market_type_id_visto:
        j.market_type_id_visto = mt_id
        print(f"   [DEBUG] mercado HT: '{mk.get('Name','')}' (mtId={mt_id})")

    aprovou, gols_pb, motivo = filtro_aprova(sh, sa, linha)
    if not aprovou:
        STATS.motivos_filtro[motivo] += 1
        return

    # Defesa: não reaposta mesma linha ou mais alta
    if linha >= j.ult_linha_apostada:
        return

    print(f"\n🎯 FC26 | {ev['name']}  [{ev['champ']}]")
    print(f"   score={sh}-{sa}  linha={linha}  gols_pra_bater={gols_pb}  "
          f"odd={sel['Price']}  live_time={ev['live_time']!r}")
    j.ult_tentativa = time.time()

    resp = await vp_apostar(ctx, jwt, ev, mk, sel, STAKE)

    STATS.tentativas += 1
    if resp["ok"]:
        STATS.aceitas += 1
    else:
        STATS.rejeitadas += 1
        STATS.por_error_type[str(resp.get("error_type"))] += 1

    csv_log(ev, j, f"{sh}-{sa}", linha, gols_pb, sel["Price"], STAKE, mt_id, resp)

    if resp["ok"]:
        j.entradas += 1
        j.ult_linha_apostada = linha
        j.falhas_seguidas = 0
        j.apostou_em.append((linha, resp["ticket_id"]))
        print(f"   ✅ APOSTOU R${STAKE}  ticket={resp['ticket_id']}  "
              f"({j.entradas}/{MAX_APOSTAS_POR_JOGO})  "
              f"tent={STATS.tentativas} ok={STATS.aceitas}")
    else:
        et = resp.get("error_type")
        if et not in ERROS_IGNORAR_BLOQUEIO:
            j.falhas_seguidas += 1
        print(f"   ❌ falhou ({j.falhas_seguidas}/{MAX_FALHAS_SEGUIDAS}): "
              f"errorType={et}  status={resp.get('status')}")
        if j.falhas_seguidas >= MAX_FALHAS_SEGUIDAS:
            j.bloqueado = True
            print(f"   🚫 JOGO BLOQUEADO: {ev['name']}")


# ╔══════════════════════════════════════════════════════════════╗
# ║  MAIN                                                         ║
# ╚══════════════════════════════════════════════════════════════╝

async def main():
    print("=" * 62)
    print(" BOT VUPI eRivals / FC26 — Over HT (score-based)")
    print("=" * 62)
    print(f"  Liga: champ contém '{LIGA_KEYWORD}' (case-insensitive)")
    print(f"  Filtro: home>=away, total>=1, gols_pra_bater ∈ [1,2]")
    print(f"  Stake: R${STAKE}  |  max apostas/jogo: {MAX_APOSTAS_POR_JOGO}")
    print(f"  Aceita mudança de odd: {ACEITA_MUDANCA_ODD}")
    print()

    pw = await async_playwright().start()
    print(f"-> conectando Chrome CDP :{CDP_PORT}...")
    browser = await pw.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
    if not browser.contexts:
        print("X Chrome sem contexts")
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
        await asyncio.sleep(2)
    print(f"  aba: {page.url}")

    # Garante domínio
    if "vupi.bet.br" not in (page.url or ""):
        print("-> navegando pra Vupi...")
        await page.goto("https://www.vupi.bet.br/",
                        wait_until="domcontentloaded")
        await asyncio.sleep(2)

    # Verifica sessão; se não logado, faz login automático
    if await vp_esta_logado(page):
        print("-> sessão já ativa")
    else:
        print("-> sem sessão, executando login automático...")
        login_ok = await vp_fazer_login(page)
        if not login_ok:
            print("⚠️ checagem de login falhou, mas vou tentar capturar "
                  "auth pelos headers mesmo assim (pode ter logado)...")

    # Captura sessionid/identity (tem fallback via header de requisição)
    print("-> auth Vupi (capturando sessionid + identity)...")
    try:
        sessionid, identity = await vp_capturar_auth(page)
        print(f"  sessionid={sessionid[:20]}...  identity={identity[:20]}...")
    except Exception as e:
        print(f"❌ Falha ao capturar auth: {e}")
        print("   Loga manual na janela do Chrome e roda de novo.")
        await pw.stop()
        return

    print("-> JWT Altenar...")
    try:
        jwt, jwt_exp = await vp_obter_jwt(ctx, sessionid, identity)
        print(f"  JWT exp em {(jwt_exp - time.time()):.0f}s")
    except Exception as e:
        print(f"❌ Falha ao obter JWT: {e}")
        await pw.stop()
        return

    csv_init()
    print(f"-> log CSV: {LOG_CSV.resolve()}")

    jogos: dict[int, JogoState] = {}
    ciclo = 0
    httpx_client = httpx.AsyncClient(timeout=10)

    print("\n" + "-" * 62)
    print("LOOP iniciado (Ctrl+C pra parar)\n")

    try:
        while True:
            ciclo += 1
            now = time.time()
            if now >= jwt_exp - 120:
                try:
                    jwt, jwt_exp = await vp_obter_jwt(ctx, sessionid, identity)
                    print(f"[{ciclo}] JWT renovado")
                except Exception as e:
                    print(f"[{ciclo}] erro renovando JWT: {e}")

            try:
                eventos = await vp_listagem(httpx_client)
            except Exception as e:
                print(f"[{ciclo}] listagem erro: {e}")
                await asyncio.sleep(SLEEP_LOOP)
                continue

            for ev in eventos:
                if ev["id"] not in jogos:
                    j = JogoState(event_id=ev["id"], nome=ev["name"], champ=ev["champ"])
                    jogos[ev["id"]] = j
                    print(f"🆕 [{ev['champ']}] sportId={ev['sport_id']}  "
                          f"{ev['name']}  score={ev['score']!r}  "
                          f"time={ev['live_time']!r}")
                await processar_jogo(ctx, jwt, httpx_client, ev, jogos[ev["id"]])

            if ciclo % 20 == 0:
                print(f"[{ciclo}] FC26_vivos={len(eventos)}  "
                      f"jogos_rastreados={len(jogos)}  "
                      f"tent={STATS.tentativas} ok={STATS.aceitas} "
                      f"rej={STATS.rejeitadas}")

            # Limpa jogos que não estão mais ao vivo
            ids_vivos = {ev["id"] for ev in eventos}
            for eid in list(jogos.keys()):
                if eid not in ids_vivos and jogos[eid].entradas == 0:
                    del jogos[eid]

            await asyncio.sleep(SLEEP_LOOP)

    except KeyboardInterrupt:
        print("\n🛑 PARADO")
    finally:
        await httpx_client.aclose()
        await pw.stop()

        print("\n" + "=" * 62)
        print("RESUMO")
        print("=" * 62)
        print(f"  Tentativas: {STATS.tentativas}")
        print(f"  Aceitas:    {STATS.aceitas}")
        print(f"  Rejeitadas: {STATS.rejeitadas}")
        print(f"  Conversão:  {STATS.conv_pct():.1f}%")
        if STATS.por_error_type:
            print(f"  Erros:")
            for et, n in STATS.por_error_type.most_common():
                print(f"    {et}: {n}")
        if STATS.motivos_filtro:
            print(f"  Vezes que o filtro REPROVOU (debug):")
            for m, n in STATS.motivos_filtro.most_common():
                print(f"    {m}: {n}")
        print(f"\n  Jogos com aposta:")
        for j in jogos.values():
            if j.entradas:
                print(f"    {j.nome} ({j.champ}): {j.entradas} apostas")
                for ln, tk in j.apostou_em:
                    print(f"       • Over {ln}  ticket={tk}")
        print(f"\n  Log completo: {LOG_CSV.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
