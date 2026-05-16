"""
orquestrador_alavancagem.py — 4 ciclos independentes de alavancagem ML:
  - Valhalla 0x0
  - Valhalla 1x1
  - Valkyrie 0x0
  - Valkyrie 1x1

Cada ciclo: 4 apostas compostas (stake × odd → próxima stake).
Sucesso do apostador = green. Falha = técnico, retry.

python orquestrador_alavancagem.py
"""

import asyncio, httpx, json, time, re, os
from datetime import datetime

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

BASE = "https://sb2frontend-altenar2.biahosted.com/api/widget"
PARAMS = {
    "culture": "pt-BR", "timezoneOffset": "180", "integration": "estrelabet",
    "deviceType": "1", "numFormat": "en-GB", "countryCode": "BR",
}
SPORT_ID = "146"
POLL_INTERVAL = 2

URL_JOGO = "https://www.estrelabet.bet.br/apostas-ao-vivo?page=liveEvent&eventId={eid}&sportId=146"

ARQUIVO_TIPS = "tips.txt"
ARQUIVO_FEEDBACK = "resultado_apostas.json"
HISTORICO_TIPS = "historico_tips.csv"
CICLO_STATE_FILE = "ciclo_state.json"

# ── 4 CICLOS INDEPENDENTES ──
STAKE_INICIAL = {
    "valhalla_0x0": 80.0,
    "valhalla_1x1": 50.0,
    "valkyrie_0x0": 50.0,
    "valkyrie_1x1": 50.0,
}

SCORES_CICLO = {
    "valhalla_0x0": (0, 0),
    "valhalla_1x1": (1, 1),
    "valkyrie_0x0": (0, 0),
    "valkyrie_1x1": (1, 1),
}

LIGA_CICLO = {
    "valhalla_0x0": "valhalla",
    "valhalla_1x1": "valhalla",
    "valkyrie_0x0": "valkyrie",
    "valkyrie_1x1": "valkyrie",
}

ALL_CICLOS = list(STAKE_INICIAL.keys())

ETAPAS_CICLO = 4
ODD_MAXIMA_ML = 1.60
MAX_APOSTAS = 1
TIMEOUT_FEEDBACK = 300


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def log(msg):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"[{t}] {msg}", flush=True)


def parse_event_name(name):
    parts = re.split(r'\s+vs\.?\s+|\s+[-–]\s+', name, maxsplit=1)
    if len(parts) != 2:
        return "", "", "", ""
    def extract(s):
        m = re.match(r'^(.+?)\s*\(([^)]+)\)\s*$', s.strip())
        return (m.group(1).strip(), m.group(2).strip()) if m else (s.strip(), s.strip())
    ta, pa = extract(parts[0])
    tb, pb = extract(parts[1])
    return ta, pa, tb, pb


def identificar_liga(champ_name):
    cl = champ_name.lower()
    if "valkyrie" in cl:
        return "valkyrie"
    if "valhalla" in cl:
        return "valhalla"
    return None


async def api_get(client, endpoint, extra):
    p = {**PARAMS, **extra}
    try:
        r = await client.get(f"{BASE}/{endpoint}", params=p, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"⚠️ API erro: {e}")
        return None


# ══════════════════════════════════════════════════════════════
# EXTRAIR ML
# ══════════════════════════════════════════════════════════════

def extrair_ml(detail):
    if not detail:
        return {}, []
    odds_map = {o["id"]: o for o in detail.get("odds", [])}
    comps = detail.get("competitors", [])
    comp_ids = [str(c.get("id", "")) for c in comps]
    ml = {}

    for mkt in detail.get("markets", []):
        mn = mkt.get("name", "").lower()
        if not ("1x2" in mn or "resultado" in mn or "vencedor" in mn):
            continue
        if "handicap" in mn or "dupla" in mn:
            continue
        odd_ids = []
        for row in mkt.get("desktopOddIds", []):
            if isinstance(row, list):
                odd_ids.extend(row)
            else:
                odd_ids.append(row)
        for oid in odd_ids:
            odd = odds_map.get(oid)
            if not odd:
                continue
            comp_id = odd.get("competitorId")
            preco = odd.get("price", 0)
            status = odd.get("oddStatus", -1)
            if comp_id and preco > 0:
                idx = comp_ids.index(str(comp_id)) if str(comp_id) in comp_ids else -1
                ml[str(comp_id)] = {
                    "odd": preco, "status": status,
                    "nome": odd.get("name", ""), "comp_index": idx,
                }
    return ml, comp_ids


# ══════════════════════════════════════════════════════════════
# CICLO DE ALAVANCAGEM
# ══════════════════════════════════════════════════════════════

class CicloAlavancagem:
    def __init__(self):
        self.ciclos = {}
        self._carregar()

    def _carregar(self):
        if os.path.exists(CICLO_STATE_FILE):
            try:
                with open(CICLO_STATE_FILE, "r") as f:
                    self.ciclos = json.load(f)
                log(f"📂 Ciclos restaurados")
            except:
                self.ciclos = {}

    def salvar(self):
        with open(CICLO_STATE_FILE, "w") as f:
            json.dump(self.ciclos, f, ensure_ascii=False, indent=2)

    def get(self, ciclo_id):
        if ciclo_id not in self.ciclos:
            self.ciclos[ciclo_id] = {
                "etapa": 0,
                "stake_atual": STAKE_INICIAL.get(ciclo_id, 10.0),
                "historico": [],
                "aguardando_resultado": False,
                "event_id_pendente": None,
                "ts_envio": 0,
                "total_ciclos_completos": 0,
            }
        return self.ciclos[ciclo_id]

    def pode_apostar(self, ciclo_id):
        return not self.get(ciclo_id)["aguardando_resultado"]

    def registrar_aposta(self, ciclo_id, event_id, stake, odd):
        c = self.get(ciclo_id)
        c["aguardando_resultado"] = True
        c["event_id_pendente"] = event_id
        c["ts_envio"] = time.time()
        c["historico"].append({
            "etapa": c["etapa"] + 1,
            "stake": stake, "odd": odd,
            "event_id": event_id,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "resultado": "pendente",
        })
        self.salvar()

    def checar_timeout(self, ciclo_id):
        c = self.get(ciclo_id)
        if not c["aguardando_resultado"]:
            return
        ts = c.get("ts_envio", 0)
        if ts and (time.time() - ts) > TIMEOUT_FEEDBACK:
            log(f"⏰ TIMEOUT {ciclo_id} — liberando.")
            c["aguardando_resultado"] = False
            c["event_id_pendente"] = None
            c["ts_envio"] = 0
            self.salvar()

    def stake_atual(self, ciclo_id):
        return self.get(ciclo_id)["stake_atual"]

    def etapa_atual(self, ciclo_id):
        return self.get(ciclo_id)["etapa"]

    def processar_green(self, ciclo_id):
        c = self.get(ciclo_id)
        c["aguardando_resultado"] = False
        c["event_id_pendente"] = None
        c["ts_envio"] = 0

        if c["historico"]:
            c["historico"][-1]["resultado"] = "green"

        ultima = c["historico"][-1] if c["historico"] else {}
        odd_usada = ultima.get("odd", 1.5)
        retorno = c["stake_atual"] * odd_usada
        c["etapa"] += 1

        if c["etapa"] >= ETAPAS_CICLO:
            lucro = retorno - STAKE_INICIAL.get(ciclo_id, 10.0)
            log(f"🏆🏆 CICLO {ciclo_id} COMPLETO! Retorno={retorno:.2f} Lucro={lucro:.2f}")
            c["total_ciclos_completos"] += 1
            c["etapa"] = 0
            c["stake_atual"] = STAKE_INICIAL.get(ciclo_id, 10.0)
            c["historico"] = []
        else:
            c["stake_atual"] = round(retorno, 2)
            log(f"✅ {ciclo_id} etapa {c['etapa']}/{ETAPAS_CICLO} — próx stake={c['stake_atual']:.2f}")

        self.salvar()

    def liberar(self, ciclo_id, motivo=""):
        c = self.get(ciclo_id)
        c["aguardando_resultado"] = False
        c["event_id_pendente"] = None
        c["ts_envio"] = 0
        self.salvar()
        log(f"🔓 {ciclo_id} liberado ({motivo})")


# ══════════════════════════════════════════════════════════════
# TIP / FEEDBACK
# ══════════════════════════════════════════════════════════════

def escrever_tip(url, time_alvo, stake, odd, score, evento, ciclo_id):
    alvo = f"ml:{time_alvo}"
    tip_line = f"{url},{alvo},0,{stake},{MAX_APOSTAS}\n"
    with open(ARQUIVO_TIPS, "a") as f:
        f.write(tip_line)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not os.path.exists(HISTORICO_TIPS):
        with open(HISTORICO_TIPS, "w") as f:
            f.write("timestamp,ciclo,evento,alvo,odd,score,stake,etapa\n")
    with open(HISTORICO_TIPS, "a") as f:
        f.write(f"{ts},{ciclo_id},{evento},{alvo},{odd},{score},{stake},?\n")

    log(f"📤 TIP: {ciclo_id} | ML {time_alvo} | odd={odd} | stake={stake} | score={score}")


def ler_feedback():
    if not os.path.exists(ARQUIVO_FEEDBACK):
        return []
    try:
        with open(ARQUIVO_FEEDBACK, "r") as f:
            content = f.read().strip()
            if not content:
                return []
            data = json.loads(content)
        open(ARQUIVO_FEEDBACK, "w").close()
        return data if isinstance(data, list) else []
    except:
        return []


# ══════════════════════════════════════════════════════════════
# ESTADO
# ══════════════════════════════════════════════════════════════

class Estado:
    def __init__(self):
        self.eventos_ativos = {}
        self.ja_apostou = {}   # {eid: ciclo_id}
        self.ciclos_poll = 0
        self.tips_enviadas = 0
        self.score_anterior = {}  # {eid: (sh, sa)} do poll anterior


# ══════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════

def dashboard(estado, ciclo_mgr):
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"{'═' * 70}")
    print(f"  🎰 ORQUESTRADOR — 4 CICLOS INDEPENDENTES | ML < {ODD_MAXIMA_ML}")
    print(f"{'═' * 70}")

    for cid in ALL_CICLOS:
        c = ciclo_mgr.get(cid)
        status = "⏳ AGUARD" if c["aguardando_resultado"] else "🟢 LIVRE"
        print(f"  {cid:>14} | Etapa {c['etapa']}/{ETAPAS_CICLO} | R${c['stake_atual']:>8.2f} | {status} | ✅ {c['total_ciclos_completos']}")

    print(f"{'─' * 70}")
    print(f"  Polls: {estado.ciclos_poll} | Tips: {estado.tips_enviadas} | Eventos: {len(estado.eventos_ativos)}")
    print(f"{'─' * 70}")

    relevantes = {eid: ev for eid, ev in estado.eventos_ativos.items()
                  if ev.get("liga_tipo") in ("valkyrie", "valhalla")}
    if not relevantes:
        print(f"  ⏳ Nenhum jogo Valkyrie/Valhalla...")
    else:
        for eid, ev in relevantes.items():
            sc = ev.get("score", [])
            s = f"{sc[0]}-{sc[1]}" if len(sc) >= 2 else "?"
            cid_usado = estado.ja_apostou.get(eid, "")
            flag = f"✅{cid_usado}" if cid_usado else "⬜"
            print(f"  {flag:>20} [{ev['liga_tipo'].upper()}] {ev.get('name','')[:40]} | {s}")

    print(f"\n  Ctrl+C = parar")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

async def main():
    estado = Estado()
    ciclo_mgr = CicloAlavancagem()

    log("🚀 4 ciclos independentes")
    for cid in ALL_CICLOS:
        log(f"   {cid}: R${STAKE_INICIAL[cid]}")
    log(f"   {ETAPAS_CICLO} etapas | ML < {ODD_MAXIMA_ML}")

    if os.path.exists(ARQUIVO_FEEDBACK):
        open(ARQUIVO_FEEDBACK, "w").close()

    last_dash = 0

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            while True:
                t0 = time.time()
                estado.ciclos_poll += 1

                # ── 1. Poll overview ──
                ov = await api_get(client, "GetLiveOverview", {"sportId": SPORT_ID})
                if not ov:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                eventos_vivos = {}
                for ev in ov.get("events", []):
                    if ev.get("status") != 1:
                        continue
                    eid = ev["id"]
                    name = ev.get("name", "")
                    score = ev.get("score", [])
                    ta, pa, tb, pb = parse_event_name(name)
                    champ = ""
                    if eid in estado.eventos_ativos:
                        champ = estado.eventos_ativos[eid].get("champ", "")
                    eventos_vivos[eid] = {
                        "name": name, "score": score,
                        "team_a": ta, "player_a": pa, "team_b": tb, "player_b": pb,
                        "champ": champ, "liga_tipo": identificar_liga(champ) or "?",
                        "mc": ev.get("mc", 0),
                    }
                estado.eventos_ativos = eventos_vivos

                # Salvar scores atuais pra comparar no próximo poll
                scores_agora = {}
                for eid, ev in eventos_vivos.items():
                    sc = ev.get("score", [])
                    sh = int(sc[0]) if len(sc) > 0 and sc[0] is not None else -1
                    sa = int(sc[1]) if len(sc) > 1 and sc[1] is not None else -1
                    scores_agora[eid] = (sh, sa)

                # ── 2. Feedback do apostador ──
                feedbacks = ler_feedback()
                for fb in feedbacks:
                    fb_status = fb.get("status", "")

                    # Achar qual ciclo está aguardando
                    ciclo_fb = None
                    for cid in ALL_CICLOS:
                        if ciclo_mgr.get(cid)["aguardando_resultado"]:
                            ciclo_fb = cid
                            break

                    if ciclo_fb:
                        if fb_status == "sucesso":
                            ciclo_mgr.processar_green(ciclo_fb)
                        elif fb_status == "falha":
                            ciclo_mgr.liberar(ciclo_fb, fb.get("motivo", "técnico"))

                # ── 3. Timeout ──
                for cid in ALL_CICLOS:
                    ciclo_mgr.checar_timeout(cid)

                # ── 4. Buscar oportunidades ──
                for eid, ev in eventos_vivos.items():
                    if eid in estado.ja_apostou:
                        continue

                    # Identificar liga
                    if ev["liga_tipo"] == "?":
                        det = await api_get(client, "GetEventDetails", {"eventId": str(eid)})
                        if det:
                            champ = det.get("champ", {}).get("name", "")
                            ev["champ"] = champ
                            ev["liga_tipo"] = identificar_liga(champ) or "?"

                    liga = ev["liga_tipo"]
                    if liga not in ("valkyrie", "valhalla"):
                        continue

                    # Score atual
                    sc = ev.get("score", [])
                    sh = int(sc[0]) if len(sc) > 0 and sc[0] is not None else 0
                    sa = int(sc[1]) if len(sc) > 1 and sc[1] is not None else 0

                    # Qual ciclo bate?
                    ciclos_match = []
                    for cid in ALL_CICLOS:
                        if LIGA_CICLO[cid] != liga:
                            continue
                        if (sh, sa) == SCORES_CICLO[cid]:
                            if ciclo_mgr.pode_apostar(cid):
                                ciclos_match.append(cid)

                    if not ciclos_match:
                        continue

                    # Confirmar: score era o mesmo no poll ANTERIOR (evita score stale)
                    score_ant = estado.score_anterior.get(eid)
                    if score_ant != (sh, sa):
                        continue  # score acabou de mudar ou é evento novo, espera próximo poll

                    # ML odds — busca detalhe
                    det = await api_get(client, "GetEventDetails", {"eventId": str(eid)})
                    if not det:
                        continue

                    ml_odds, comp_ids = extrair_ml(det)
                    if not ml_odds:
                        continue

                    # Favorito odd < ODD_MAXIMA_ML
                    melhor = None
                    for cid_ml, info in ml_odds.items():
                        if info["status"] != 0:
                            continue
                        if info["odd"] <= 0 or info["odd"] >= ODD_MAXIMA_ML:
                            continue
                        if melhor is None or info["odd"] < melhor["odd"]:
                            melhor = {**info, "comp_id": cid_ml}

                    if not melhor:
                        continue

                    # Determinar time do favorito pelo NOME da odd, não pelo index
                    odd_nome = melhor.get("nome", "").lower()
                    ta = ev.get("team_a", "")
                    tb = ev.get("team_b", "")

                    if ta and ta.lower() in odd_nome:
                        time_alvo = ta
                    elif tb and tb.lower() in odd_nome:
                        time_alvo = tb
                    elif ta and odd_nome in ta.lower():
                        time_alvo = ta
                    elif tb and odd_nome in tb.lower():
                        time_alvo = tb
                    else:
                        # Fallback: usa o nome da odd direto
                        time_alvo = melhor.get("nome", "")
                        log(f"⚠️ Não casou nome '{odd_nome}' com '{ta}'/'{tb}' — usando '{time_alvo}'")

                    if not time_alvo:
                        continue

                    log(f"🔍 Favorito: {time_alvo} odd={melhor['odd']} | {ev.get('name','')}")

                    # 1 tip por jogo, primeiro ciclo que bate
                    ciclo_escolhido = ciclos_match[0]
                    stake = ciclo_mgr.stake_atual(ciclo_escolhido)
                    etapa = ciclo_mgr.etapa_atual(ciclo_escolhido) + 1
                    url = URL_JOGO.format(eid=eid)
                    score_str = f"{sh}-{sa}"

                    escrever_tip(url, time_alvo, stake, melhor["odd"], score_str, ev.get("name", ""), ciclo_escolhido)
                    ciclo_mgr.registrar_aposta(ciclo_escolhido, eid, stake, melhor["odd"])
                    estado.ja_apostou[eid] = ciclo_escolhido
                    estado.tips_enviadas += 1

                    log(f"🎰 {ciclo_escolhido} ETAPA {etapa}/{ETAPAS_CICLO} | ML {time_alvo} | odd={melhor['odd']} | stake={stake:.2f} | score={score_str}")

                # ── 5. Limpar ──
                for eid in list(estado.ja_apostou.keys()):
                    if eid not in eventos_vivos:
                        del estado.ja_apostou[eid]

                # ── 6. Atualizar scores pro próximo poll ──
                estado.score_anterior = scores_agora

                # ── Dashboard ──
                if time.time() - last_dash >= 3:
                    dashboard(estado, ciclo_mgr)
                    last_dash = time.time()

                wait = max(0, POLL_INTERVAL - (time.time() - t0))
                if wait > 0:
                    await asyncio.sleep(wait)

        except KeyboardInterrupt:
            log("⏹️ Parado.")
            ciclo_mgr.salvar()

    print(f"\n⏹️ Tips enviadas: {estado.tips_enviadas}")


if __name__ == "__main__":
    asyncio.run(main())