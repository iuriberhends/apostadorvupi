"""
recon_erivals.py — descobre sportId, ChampName, marketTypeId do eRivals/FC26 na Vupi.

Lista TODOS os live events da Vupi (sem filtro de esporte), agrupa por sportId/champ,
filtra candidatos eRivals/FC26/FIFA, e pra UM evento candidato busca o detalhe
completo pra listar TODOS os mercados (com market_type_id e seleções).

USO:
    python recon_erivals.py

OUTPUT:
    Console: panorama imediato
    vupi_erivals_recon.json: detalhe completo pra análise

Rode quando houver jogo eRivals AO VIVO na Vupi (normalmente 24/7).
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx


VP_FRONT = "https://sb2frontend-altenar2.biahosted.com"
VP_PARAMS = {
    "culture": "pt-BR", "timezoneOffset": 180, "integration": "vupi",
    "deviceType": 1, "numFormat": "en-GB", "countryCode": "BR",
}
VP_HEADERS = {
    "Origin": "https://www.vupi.bet.br",
    "Referer": "https://www.vupi.bet.br/",
    "Accept": "application/json, text/plain, */*",
}

KEYWORDS_LIGA = ("rivals", "fc 26", "fc26", "fifa", "esoccer", "e-soccer", "ea fc")


async def listar_todos_live(client):
    p = {**VP_PARAMS, "eventCount": 0}
    r = await client.get(
        f"{VP_FRONT}/api/Sportsbook/GetLiveEvents",
        params=p, headers=VP_HEADERS, timeout=20,
    )
    r.raise_for_status()
    return r.json()


async def detalhar_evento(client, event_id):
    p = {**VP_PARAMS, "eventId": event_id}
    r = await client.get(
        f"{VP_FRONT}/api/Sportsbook/GetEventDetails",
        params=p, headers=VP_HEADERS, timeout=15,
    )
    r.raise_for_status()
    return r.json().get("Result", {})


def extrair_mercados(detalhe):
    out = []
    for mg in detalhe.get("MarketGroups", []) or []:
        for mk in mg.get("Markets") or mg.get("Items") or []:
            mtid = mk.get("OrgMarketTypeId") or mk.get("MarketTypeId")
            selecoes = []
            for s in (mk.get("Selections") or mk.get("Items")
                      or mk.get("Odds") or []):
                selecoes.append({
                    "sel_type_id": s.get("SelectionTypeId"),
                    "name": s.get("Name", ""),
                    "spov": s.get("SPOV", ""),
                    "price": s.get("Price"),
                    "is_active": s.get("IsActive"),
                })
            out.append({
                "market_type_id": mtid,
                "market_name": mk.get("Name", ""),
                "status": mk.get("Status"),
                "selections": selecoes,
            })
    return out


async def main():
    saida = {"ts": datetime.now(timezone.utc).isoformat()}

    async with httpx.AsyncClient() as client:
        print("=" * 70)
        print(" RECON Vupi — descobrindo eRivals / FC26 / FIFA")
        print("=" * 70)

        print("\n[1] GET /Sportsbook/GetLiveEvents (sem filtro de esporte)...")
        try:
            data = await listar_todos_live(client)
        except Exception as e:
            print(f"   ❌ Falha: {e}")
            return

        eventos = []
        for sport in data.get("Result", {}).get("Items", []) or []:
            for champ in sport.get("Items", []) or []:
                for ev in champ.get("Events", []) or []:
                    eventos.append({
                        "sport_id": sport.get("Id", 0),
                        "sport_name": sport.get("Name", ""),
                        "champ_id": champ.get("Id", 0),
                        "champ_name": champ.get("Name", ""),
                        "event_id": ev.get("Id"),
                        "event_name": ev.get("Name", ""),
                        "live_time": ev.get("LiveCurrentTime", ""),
                        "live_score": ev.get("LiveScore", ""),
                        "category_name": ev.get("CategoryName", ""),
                    })

        print(f"   Total de eventos vivos: {len(eventos)}")
        if not eventos:
            print("\n⚠️ Nenhum evento vivo. Tente rodar quando houver jogo.")
            return

        print("\n[2] Esportes com jogos vivos agora:")
        sport_counts = {}
        for e in eventos:
            k = (e["sport_id"], e["sport_name"])
            sport_counts[k] = sport_counts.get(k, 0) + 1
        for (sid, sname), n in sorted(sport_counts.items()):
            print(f"   sportId={sid:>4}  {sname:<35} {n:>3} jogos")

        print("\n[3] Ligas (champs) por esporte:")
        champs_por_sport = {}
        for e in eventos:
            sid = e["sport_id"]
            champs_por_sport.setdefault(sid, set()).add(
                (e["sport_name"], e["champ_name"])
            )
        for sid in sorted(champs_por_sport):
            print(f"   sportId={sid}:")
            for sname, cname in sorted(champs_por_sport[sid]):
                print(f"      • {cname}")

        print(f"\n[4] Filtrando por keywords {KEYWORDS_LIGA}:")
        candidatos = [
            e for e in eventos
            if any(kw in e["champ_name"].lower() for kw in KEYWORDS_LIGA)
        ]
        print(f"   {len(candidatos)} candidato(s) encontrado(s)")
        for e in candidatos[:15]:
            print(f"   sportId={e['sport_id']} | {e['champ_name']} | "
                  f"{e['event_name']!r}")
            print(f"       live_time={e['live_time']!r}  "
                  f"live_score={e['live_score']!r}")

        if not candidatos:
            print("\n⚠️ Nenhum candidato. Confira manualmente os ChampNames acima.")
            saida["todos_eventos"] = eventos
            saida["sport_counts"] = {f"{k[0]}_{k[1]}": v for k, v in sport_counts.items()}
            Path("vupi_erivals_recon.json").write_text(
                json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            print(f"\n💾 Salvo em vupi_erivals_recon.json (dump completo)")
            return

        alvo = candidatos[0]
        print(f"\n[5] GET /GetEventDetails do evento {alvo['event_id']} "
              f"({alvo['event_name']})...")
        try:
            det = await detalhar_evento(client, alvo["event_id"])
        except Exception as e:
            print(f"   ❌ Falha: {e}")
            return

        mercados = extrair_mercados(det)
        print(f"   {len(mercados)} mercados encontrados:\n")
        for m in mercados:
            print(f"   mtId={m['market_type_id']:>6}  status={m['status']}  "
                  f"{m['market_name']}")
            for s in m["selections"][:4]:
                print(f"      selType={s['sel_type_id']}  name={s['name']!r}  "
                      f"spov={s['spov']!r}  @ {s['price']}  "
                      f"active={s['is_active']}")

        saida["sport_counts"] = {
            f"{k[0]}_{k[1]}": v for k, v in sport_counts.items()
        }
        saida["candidatos"] = candidatos
        saida["amostra_detalhe"] = {
            "evento": alvo,
            "mercados": mercados,
        }
        Path("vupi_erivals_recon.json").write_text(
            json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"\n💾 Salvo em vupi_erivals_recon.json")


if __name__ == "__main__":
    asyncio.run(main())
