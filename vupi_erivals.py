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
LIGA_KEYWORD = "fc26"   # case-insensitive, ignora espaços ("FC 26" também passa)

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
    """Verifica login via localStorage (marcadores Vupi) com fallback DOM.

    Sinais que a Vupi grava no localStorage SÓ quando logado:
      - eb-identity     (token de identidade)
      - profileInfos    (info do perfil)
      - login           (flag)
      - lastActiveSessionTime  (timestamp de sessão)
    Se qualquer um desses estiver presente → logado.
    Senão, fallback pra checagem visual (saldo / botão Entrar).
    """
    try:
        result = await page.evaluate("""
            () => {
                // ── 1) Marcadores fortes no localStorage ──
                const STRONG_MARKERS = [
                    'eb-identity', 'profileInfos', 'login',
                    'lastActiveSessionTime',
                ];
                for (const key of STRONG_MARKERS) {
                    const v = localStorage.getItem(key);
                    if (v && v.length > 0) {
                        return { logged: true, via: 'localStorage:' + key };
                    }
                }

                // ── 2) Fallback DOM: saldo visível ──
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
                            return { logged: true, via: 'balance', value: text.slice(0, 40) };
                        }
                    }
                }

                // ── 3) Botão Entrar visível → deslogado ──
                const candidates = document.querySelectorAll('button, a');
                for (const el of candidates) {
                    const t = (el.innerText || '').trim().toLowerCase();
                    if (t === 'entrar' || t === 'login' || t === 'entrar / cadastrar') {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {
                            return { logged: false, via: 'entrar_button_visible' };
                        }
                    }
                }

                // Inconclusivo
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
    """Login automático na Vupi. Click via JS, espera modal, preenche, submete."""
    print("  🔐 fazendo login automático na Vupi...")

    if "vupi.bet.br" not in (page.url or ""):
        await page.goto("https://www.vupi.bet.br/",
                        wait_until="domcontentloaded")
        await asyncio.sleep(2)

    # 1. Clica em "Entrar" via JS (mesma lógica que vp_esta_logado usa pra detectar)
    clicked = await page.evaluate("""
        () => {
            const candidates = document.querySelectorAll('button, a, [role="button"], div[onclick]');
            for (const el of candidates) {
                const t = (el.innerText || '').trim().toLowerCase();
                if (t === 'entrar' || t === 'login' || t === 'entrar / cadastrar'
                    || (t.startsWith('entrar') && t.length < 25)) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        el.click();
                        return { ok: true, text: t, tag: el.tagName };
                    }
                }
            }
            return { ok: false };
        }
    """)
    if not clicked.get("ok"):
        print("    ❌ botão Entrar não achado pra clicar")
        return False
    print(f"    ✓ clicou em <{clicked.get('tag')}> '{clicked.get('text')}'")

    # 2. Aguarda campo de SENHA aparecer (sinal robusto que o modal abriu)
    print("    aguardando modal de login abrir...")
    senha_field = None
    for _ in range(20):  # ~10s
        try:
            f = page.locator('input[type="password"]').first
            if await f.is_visible(timeout=500):
                senha_field = f
                break
        except Exception:
            pass
        await asyncio.sleep(0.5)
    if not senha_field:
        print("    ❌ modal não abriu em 10s (campo senha não apareceu)")
        return False
    print("    ✓ modal aberto")

    # 3. Acha campo de email = primeiro input visível que NÃO é password
    inputs_info = await page.evaluate("""
        () => {
            const inputs = document.querySelectorAll('input');
            return Array.from(inputs)
                .map((el, idx) => {
                    const r = el.getBoundingClientRect();
                    return {
                        idx: idx,
                        visible: r.width > 0 && r.height > 0,
                        type: el.type,
                        name: el.name || '',
                        placeholder: el.placeholder || '',
                        testid: el.getAttribute('data-testid') || '',
                        autocomplete: el.autocomplete || '',
                    };
                })
                .filter(i => i.visible);
        }
    """)
    print(f"    inputs visíveis: {len(inputs_info)}")
    for inp in inputs_info:
        print(f"      [{inp['idx']}] type={inp['type']!r} name={inp['name']!r} "
              f"testid={inp['testid']!r} placeholder={inp['placeholder']!r}")

    # Procura o melhor candidato pra email
    email_idx = None
    for inp in inputs_info:
        t = inp["type"]
        if t in ("password", "hidden", "submit", "button", "checkbox", "radio"):
            continue
        # Prioridade 1: type=email
        if t == "email":
            email_idx = inp["idx"]; break
        # Prioridade 2: name/testid/placeholder contém email|cpf|user
        marker = f"{inp['name']} {inp['testid']} {inp['placeholder']} {inp['autocomplete']}".lower()
        if any(x in marker for x in ("email", "cpf", "user", "login")):
            email_idx = inp["idx"]; break
    # Fallback: primeiro input texto/sem type
    if email_idx is None:
        for inp in inputs_info:
            if inp["type"] in ("text", ""):
                email_idx = inp["idx"]; break

    if email_idx is None:
        print("    ❌ campo de email não achado entre os inputs visíveis")
        return False

    print(f"    campo email: índice {email_idx}")
    email_locator = page.locator("input").nth(email_idx)
    await email_locator.click()
    await email_locator.fill("")
    await email_locator.type(EMAIL_VUPI, delay=random.randint(30, 80))

    # 4. Preenche senha
    await senha_field.click()
    await senha_field.fill("")
    await senha_field.type(SENHA_VUPI, delay=random.randint(30, 70))
    await asyncio.sleep(0.3)

    # 5. Submete via JS (acha botão de submit dentro do form de login)
    submitted_via = await page.evaluate("""
        () => {
            // Procura botão submit dentro do form que contém o input password
            const pwd = document.querySelector('input[type="password"]');
            if (!pwd) return null;
            let form = pwd.closest('form');
            const root = form || document;
            // Tenta submit explícito
            let btn = root.querySelector('button[type="submit"]');
            if (btn) { btn.click(); return 'button[type=submit]'; }
            // Tenta data-testid
            btn = root.querySelector('[data-testid*="submit" i],[data-testid*="login" i]');
            if (btn && btn.tagName === 'BUTTON') { btn.click(); return btn.getAttribute('data-testid'); }
            // Tenta qualquer botão com texto "entrar"/"login"
            const btns = root.querySelectorAll('button, [role="button"]');
            for (const b of btns) {
                const t = (b.innerText || '').trim().toLowerCase();
                if (t === 'entrar' || t === 'login' || t === 'acessar') {
                    const r = b.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        b.click();
                        return `text=${t}`;
                    }
                }
            }
            return null;
        }
    """)
    if submitted_via:
        print(f"    submit via JS: {submitted_via}")
    else:
        await senha_field.press("Enter")
        print("    submit via Enter")

    # 6. Aguarda confirmação de login (saldo visível ou Entrar sumiu)
    print("    aguardando login completar (30s)...")
    for _ in range(60):
        if await vp_esta_logado(page):
            print("    ✅ logado")
            await asyncio.sleep(1)
            return True
        await asyncio.sleep(0.5)

    print("    ⚠️ timeout — saldo não apareceu / Entrar ainda visível")
    print("    dump de storage pra debug:")
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
    # Normaliza a keyword do mesmo jeito que normaliza o champ (lower + sem espaço)
    kw = LIGA_KEYWORD.lower().replace(" ", "")
    out = []
    for sport in data.get("Result", {}).get("Items", []) or []:
        for champ in sport.get("Items", []) or []:
            champ_name_top = champ.get("Name", "")
            for ev in champ.get("Events", []) or []:
                # ChampName pode vir no ev ou no champ pai
                champ_name = ev.get("ChampName", "") or champ_name_top
                norm = champ_name.lower().replace(" ", "")
                if kw not in norm:
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

    print(f"\n🎯 {LIGA_KEYWORD.upper()} | {ev['name']}  [{ev['champ']}]")
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
                print(f"[{ciclo}] {LIGA_KEYWORD}_vivos={len(eventos)}  "
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
