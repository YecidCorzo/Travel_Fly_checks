#!/usr/bin/env python3
"""
Vigilante de precios de vuelos Estocolmo -> Barcelona (Semana Santa 2027).

Busca en Google Flights con Playwright, filtra por escalas y aerolíneas,
guarda el historial de precios y avisa cuando el precio baja de verdad
(nuevo mínimo histórico o por debajo de tu precio objetivo).

Uso:
    python vigilante.py              # una comprobación (para el programador de tareas)
    python vigilante.py --loop       # se queda abierto y comprueba cada hora
    python vigilante.py --test-aviso # envía una notificación de prueba
    python vigilante.py --visible    # abre el navegador a la vista (para depurar)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
HISTORY_PATH = BASE / "historial_precios.json"
LOG_PATH = BASE / "vigilante.log"


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(
            "No encuentro config.json. Copia config.example.json a config.json "
            "y ajústalo (ver README)."
        )
    return load_json(CONFIG_PATH, {})


# --------------------------------------------------------------------------- #
# Lectura de resultados de Google Flights
# --------------------------------------------------------------------------- #
@dataclass
class Vuelo:
    precio: int
    escalas: int
    aerolineas: str
    salida: str
    duracion: str
    texto: str


PRICE_RE = re.compile(r"(\d[\d.,\u00a0\u202f ]*\d|\d)\s*(?:Swedish kronor|SEK|kr)\b|\bSEK\s*(\d[\d.,\u00a0\u202f ]*\d)", re.I)
SELECTOR = "[aria-label*='kronor'], [aria-label*='SEK']"
STOPS_RE = re.compile(r"(Nonstop|Direct|(\d+)\s+stops?)\b", re.I)
AIRLINE_RE = re.compile(r"flight with\s+(.+?)\.", re.I)
LEAVES_RE = re.compile(r"Leaves\s+(.+?)\s+and arrives", re.I)
DURATION_RE = re.compile(r"Total duration\s+(.+?)\.", re.I)


def parse_aria_label(label: str) -> Vuelo | None:
    """Convierte la etiqueta accesible de un resultado de Google Flights en un Vuelo."""
    m_price = PRICE_RE.search(label)
    m_stops = STOPS_RE.search(label)
    if not m_price or not m_stops:
        return None
    precio = int(re.sub(r"\D", "", m_price.group(1) or m_price.group(2)) or 0)
    escalas = int(m_stops.group(2)) if m_stops.group(2) else 0
    m_air = AIRLINE_RE.search(label)
    aerolineas = m_air.group(1).strip() if m_air else None
    if not aerolineas:
        # Plan B: buscar nombres de aerolíneas conocidas en el texto de la tarjeta
        conocidas = ["SAS", "Norwegian", "Lufthansa", "KLM", "Air France", "Ryanair",
                     "Vueling", "easyJet", "Finnair", "Swiss", "Austrian", "Brussels",
                     "Eurowings", "Iberia", "Wizz", "airBaltic", "Turkish", "LOT",
                     "Condor", "TAP", "Edelweiss", "Lauda"]
        vistos = [a for a in conocidas if re.search(rf"\b{re.escape(a)}\b", label)]
        aerolineas = " and ".join(vistos) if vistos else "?"
    m_leave = LEAVES_RE.search(label)
    m_dur = DURATION_RE.search(label)
    return Vuelo(
        precio=precio,
        escalas=escalas,
        aerolineas=aerolineas,
        salida=m_leave.group(1).strip() if m_leave else "?",
        duracion=m_dur.group(1).strip() if m_dur else "?",
        texto=label,
    )


def aerolinea_permitida(vuelo: Vuelo, permitidas: list[str]) -> bool:
    """Todas las aerolíneas del vuelo deben estar en la lista permitida."""
    if not permitidas:
        return True
    partes = re.split(r",\s*|\s+and\s+", vuelo.aerolineas)
    partes = [p.strip().lower() for p in partes if p.strip()]
    perm = [p.lower() for p in permitidas]
    return bool(partes) and all(any(a in p for a in perm) for p in partes)


def google_flights_url(origen: str, destino: str, ida: str, vuelta: str | None,
                       adultos: int, max_escalas: int) -> str:
    """Si vuelta es None, busca solo ida (one way)."""
    escalas_txt = "nonstop" if max_escalas == 0 else f"{max_escalas} stop or fewer"
    if vuelta:
        q = (f"Flights from {origen} to {destino} on {ida} through {vuelta} "
             f"for {adultos} adults {escalas_txt}")
    else:
        q = (f"One way flights from {origen} to {destino} on {ida} "
             f"for {adultos} adults {escalas_txt}")
    params = {"q": q, "hl": "en", "gl": "SE", "curr": "SEK"}
    return "https://www.google.com/travel/flights?" + urllib.parse.urlencode(params)


def aceptar_cookies(page) -> None:
    for texto in ("Reject all", "Accept all", "Avvisa alla", "Godkänn alla"):
        try:
            btn = page.get_by_role("button", name=texto)
            if btn.count() > 0:
                btn.first.click(timeout=3000)
                page.wait_for_load_state("domcontentloaded")
                return
        except Exception:
            pass


def buscar(page, url: str, nombre: str, cfg: dict) -> list[Vuelo]:
    log(f"Buscando '{nombre}': {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    aceptar_cookies(page)
    try:
        page.wait_for_selector(SELECTOR, timeout=30000)
    except Exception:
        page.screenshot(path=str(BASE / "ultimo_error.png"), full_page=True)
        (BASE / "ultimo_error.html").write_text(page.content(), encoding="utf-8")
        raise RuntimeError(f"No aparecieron resultados en {page.url} "
                           "(captura en ultimo_error.png)")
    page.wait_for_timeout(2500)  # deja que terminen de cargar los precios

    # Abre "más vuelos" si existe, para no perder opciones
    try:
        mas = page.get_by_role("button", name=re.compile(r"more flights", re.I))
        if mas.count() > 0:
            mas.first.click(timeout=3000)
            page.wait_for_timeout(2500)
    except Exception:
        pass

    # Junta, por cada tarjeta de vuelo (<li>), todas sus etiquetas y su texto visible
    labels = page.evaluate("""(sel) => {
        const out = [], vistos = new Set();
        for (const el of document.querySelectorAll(sel)) {
            const card = el.closest('li') || el;
            if (vistos.has(card)) continue;
            vistos.add(card);
            const partes = [card.getAttribute('aria-label') || ''];
            for (const a of card.querySelectorAll('[aria-label]'))
                partes.push(a.getAttribute('aria-label'));
            partes.push((card.innerText || '').replace(/\\s+/g, ' '));
            out.push(partes.filter(Boolean).join(' | '));
        }
        return out;
    }""", SELECTOR)
    try:
        (BASE / "debug_resultados.txt").write_text(
            f"{page.url}\n\n" + "\n\n".join(labels), encoding="utf-8")
    except OSError:
        pass
    vuelos, vistos = [], set()
    for lab in labels:
        v = parse_aria_label(lab or "")
        if not v or v.texto in vistos:
            continue
        vistos.add(v.texto)
        if v.escalas <= cfg["max_escalas"] and aerolinea_permitida(v, cfg["aerolineas"]):
            vuelos.append(v)
    vuelos.sort(key=lambda v: v.precio)
    log(f"  {len(labels)} resultados, {len(vuelos)} cumplen los filtros")
    if labels and not vuelos:
        for lab in labels[:2]:
            v = parse_aria_label(lab or "")
            log(f"    ejemplo leído: {asdict(v) if v else 'no se pudo leer'} <- {lab[:200]}")
    return vuelos


@dataclass
class Opcion:
    precio: int
    tipo: str      # "ida y vuelta" o "combinada"
    detalle: str
    url: str


def mejor_opcion(page, b: dict, cfg: dict) -> Opcion | None:
    """
    Devuelve la opción más barata para una búsqueda:
      - billete de ida y vuelta (misma reserva), o
      - combinación de dos billetes de solo ida, que permite mezclar
        aerolíneas (p. ej. ida con SAS y vuelta con Norwegian).
    """
    o, d, n, esc = cfg["origen"], cfg["destino"], b["adultos"], cfg["max_escalas"]
    opciones: list[Opcion] = []

    # 1) Ida y vuelta en la misma reserva
    url_iv = google_flights_url(o, d, b["ida"], b["vuelta"], n, esc)
    try:
        iv = buscar(page, url_iv, f"{b['nombre']} · ida y vuelta", cfg)
        if iv:
            v = iv[0]
            opciones.append(Opcion(
                v.precio, "ida y vuelta",
                f"{v.aerolineas} ({v.escalas} escala/s), sale {v.salida}", url_iv))
    except Exception as e:
        log(f"  ERROR ida y vuelta: {e}")

    # 2) Combinación de dos billetes de solo ida
    if cfg.get("combinar_aerolineas", True):
        url_ida = google_flights_url(o, d, b["ida"], None, n, esc)
        url_vta = google_flights_url(d, o, b["vuelta"], None, n, esc)
        try:
            idas = buscar(page, url_ida, f"{b['nombre']} · solo ida", cfg)
            vueltas = buscar(page, url_vta, f"{b['nombre']} · solo vuelta", cfg)
            if idas and vueltas:
                vi, vv = idas[0], vueltas[0]
                opciones.append(Opcion(
                    vi.precio + vv.precio, "combinada",
                    f"Ida {vi.aerolineas} {vi.precio} SEK ({vi.escalas} escala/s) + "
                    f"Vuelta {vv.aerolineas} {vv.precio} SEK ({vv.escalas} escala/s)",
                    url_ida))
        except Exception as e:
            log(f"  ERROR combinación: {e}")

    if not opciones:
        return None
    mejor = min(opciones, key=lambda x: x.precio)
    for op in opciones:
        marca = "  <-- mejor" if op is mejor else ""
        log(f"  {op.tipo}: {op.precio} SEK · {op.detalle}{marca}")
    return mejor


# --------------------------------------------------------------------------- #
# Notificaciones
# --------------------------------------------------------------------------- #
def notificar(titulo: str, mensaje: str, cfg: dict, url: str | None = None) -> None:
    avisos = cfg.get("notificaciones", {})

    # 1) Notificación en el escritorio
    if avisos.get("escritorio", True):
        try:
            from plyer import notification
            notification.notify(title=titulo, message=mensaje[:250],
                                app_name="Vigilante de vuelos", timeout=15)
        except Exception as e:
            log(f"  (sin notificación de escritorio: {e})")

    # 2) Notificación al móvil con ntfy.sh (gratis, sin cuenta)
    tema = avisos.get("ntfy_tema")
    if tema:
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{tema}",
                data=mensaje.encode("utf-8"),
                headers={
                    "Title": titulo.encode("ascii", "ignore").decode() or "Vuelos",
                    "Tags": "airplane",
                    **({"Click": url} if url else {}),
                },
            )
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            log(f"  (fallo ntfy: {e})")


# --------------------------------------------------------------------------- #
# Lógica principal
# --------------------------------------------------------------------------- #
def comprobar(cfg: dict, visible: bool = False) -> None:
    from playwright.sync_api import sync_playwright

    historial = load_json(HISTORY_PATH, {"registros": [], "minimos": {}})
    ahora = datetime.now().isoformat(timespec="minutes")
    resultados: dict[str, Opcion | None] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not visible)
        context = browser.new_context(locale="en-GB", timezone_id="Europe/Stockholm")
        page = context.new_page()
        for b in cfg["busquedas"]:
            resultados[b["nombre"]] = mejor_opcion(page, b, cfg)
        browser.close()

    if any(v is None for v in resultados.values()):
        log("Alguna búsqueda falló o no tuvo vuelos válidos; no se compara el total.")
        historial["registros"].append({
            "fecha": ahora,
            "busquedas": {k: asdict(v) if v else None for k, v in resultados.items()},
        })
        save_json(HISTORY_PATH, historial)
        return

    total = sum(v.precio for v in resultados.values())
    minimo_anterior = historial["minimos"].get("total")
    objetivo = cfg.get("precio_objetivo_total")
    bajada_min = cfg.get("bajada_minima_sek", 0)

    historial["registros"].append({
        "fecha": ahora,
        "total": total,
        "busquedas": {k: asdict(v) for k, v in resultados.items()},
    })
    log(f"TOTAL para todo el grupo: {total} SEK "
        f"(mínimo anterior: {minimo_anterior if minimo_anterior else '—'})")

    motivos = []
    if minimo_anterior is None:
        motivos.append("primer precio registrado")
    elif total <= minimo_anterior - bajada_min:
        motivos.append(f"nuevo mínimo: baja {minimo_anterior - total} SEK")
    if objetivo and total <= objetivo:
        motivos.append(f"por debajo de tu objetivo de {objetivo} SEK")

    if minimo_anterior is None or total < minimo_anterior:
        historial["minimos"]["total"] = total
        historial["minimos"]["fecha"] = ahora
    save_json(HISTORY_PATH, historial)

    if motivos:
        lineas = [f"{k}: {v.precio} SEK [{v.tipo}] {v.detalle}"
                  for k, v in resultados.items()]
        mensaje = (f"Total {total} SEK — " + "; ".join(motivos) + "\n" +
                   "\n".join(lineas))
        url = next(iter(resultados.values())).url
        notificar("✈️ Bajada de precio Estocolmo–Barcelona", mensaje, cfg, url)
        log("Notificación enviada: " + "; ".join(motivos))
    else:
        log("Sin bajada real; no se envía aviso.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Vigilante de precios de vuelos")
    ap.add_argument("--loop", action="store_true", help="comprobar cada hora sin parar")
    ap.add_argument("--visible", action="store_true", help="mostrar el navegador")
    ap.add_argument("--test-aviso", action="store_true", help="enviar aviso de prueba")
    args = ap.parse_args()
    cfg = load_config()

    if args.test_aviso:
        notificar("✈️ Prueba", "Si ves esto, los avisos funcionan.", cfg)
        log("Aviso de prueba enviado.")
        return

    intervalo = int(cfg.get("intervalo_minutos", 60)) * 60
    while True:
        try:
            comprobar(cfg, visible=args.visible)
        except Exception as e:
            log(f"ERROR general: {e}")
        if not args.loop:
            break
        log(f"Próxima comprobación en {intervalo // 60} minutos.")
        time.sleep(intervalo)


if __name__ == "__main__":
    main()
