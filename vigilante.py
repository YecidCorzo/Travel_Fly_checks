#!/usr/bin/env python3
"""
Vigilante de precios de vuelos Estocolmo -> Barcelona (Semana Santa 2027).

Busca en Google Flights con Playwright, filtra por escalas y aerolíneas,
compara ida y vuelta con combinaciones de dos billetes de solo ida,
guarda el historial, genera un informe HTML (informe.html) y avisa cuando
el precio baja de verdad.

Uso:
    python vigilante.py              # una comprobación (para el programador de tareas)
    python vigilante.py --loop       # se queda abierto y comprueba cada hora
    python vigilante.py --visible    # abre el navegador a la vista (para depurar)
    python vigilante.py --abrir      # abre informe.html al terminar
    python vigilante.py --test-aviso # envía una notificación de prueba
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
HISTORY_PATH = BASE / "historial_precios.json"
LOG_PATH = BASE / "vigilante.log"
REPORT_PATH = BASE / "informe.html"


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
        sys.exit("No encuentro config.json. Copia config.example.json a config.json "
                 "y ajústalo (ver README).")
    cfg = load_json(CONFIG_PATH, {})
    cfg.setdefault("aeropuerto_origen", "ARN")
    cfg.setdefault("aeropuerto_destino", "BCN")
    return cfg


def sek(n: int | None) -> str:
    return "—" if n is None else f"{n:,}".replace(",", " ") + " kr"


# --------------------------------------------------------------------------- #
# Lectura de resultados de Google Flights
# --------------------------------------------------------------------------- #
@dataclass
class Vuelo:
    precio: int
    escalas: int
    aerolineas: str
    salida: str
    llegada: str
    duracion: str
    texto: str = field(repr=False, default="")


NUM = r"\d[\d.,   ]*\d|\d"
PRICE_RE = re.compile(
    rf"(?:({NUM})\s*(?:Swedish kronor|SEK|kr)\b|\bSEK\s*({NUM}))", re.I)
STOPS_RE = re.compile(r"\b(Nonstop|Direct|(\d+)\s+stops?)\b", re.I)
AIRLINE_RE = re.compile(r"flight with\s+(.+?)\.", re.I)
DURATION_RE = re.compile(r"Total duration\s+(.+?)\.", re.I)
DURATION_TXT_RE = re.compile(r"\b(\d+\s*hr?(?:\s*\d+\s*min)?)\b", re.I)
TIMES_RE = re.compile(
    r"(\d{1,2}:\d{2}(?:\s?[AP]M)?)\s*[–—-]\s*(\d{1,2}:\d{2}(?:\s?[AP]M)?(?:\+\d)?)", re.I)
AEROLINEAS_CONOCIDAS = [
    "SAS", "Norwegian", "Lufthansa", "KLM", "Air France", "Ryanair", "Vueling",
    "easyJet", "Finnair", "Swiss", "Austrian", "Brussels", "Eurowings", "Iberia",
    "Wizz", "airBaltic", "Turkish", "LOT", "Condor", "TAP", "Edelweiss", "Lauda",
]
SELECTOR = "[aria-label*='kronor'], [aria-label*='SEK']"


def _precio(m: re.Match) -> int:
    return int(re.sub(r"\D", "", m.group(1) or m.group(2)) or 0)


def parse_tarjeta(principal: str, texto: str) -> Vuelo | None:
    """
    principal: etiqueta accesible del vuelo ("From 1,234 Swedish kronor ... flight with SAS ...")
    texto: texto visible de la tarjeta (horas, aerolínea, duración, escalas, precio).
    El precio se toma SOLO de la etiqueta principal; si no hay, del último precio
    del texto visible (en las tarjetas el precio va al final).
    """
    precio = 0
    m = PRICE_RE.search(principal or "")
    if m:
        precio = _precio(m)
    else:
        todos = list(PRICE_RE.finditer(texto or ""))
        if todos:
            precio = _precio(todos[-1])
    if precio <= 0:
        return None

    m_stops = STOPS_RE.search(principal or "") or STOPS_RE.search(texto or "")
    if not m_stops:
        return None
    escalas = int(m_stops.group(2)) if m_stops.group(2) else 0

    m_air = AIRLINE_RE.search(principal or "")
    if m_air:
        aerolineas = m_air.group(1).strip()
    else:
        vistos = [a for a in AEROLINEAS_CONOCIDAS
                  if re.search(rf"\b{re.escape(a)}\b", texto or "")]
        aerolineas = " and ".join(vistos) if vistos else "?"

    m_t = TIMES_RE.search(texto or "")
    m_d = DURATION_RE.search(principal or "") or DURATION_TXT_RE.search(texto or "")
    return Vuelo(
        precio=precio,
        escalas=escalas,
        aerolineas=aerolineas,
        salida=m_t.group(1) if m_t else "?",
        llegada=m_t.group(2) if m_t else "?",
        duracion=m_d.group(1).strip() if m_d else "?",
        texto=f"{principal} || {texto}",
    )


def minutos_duracion(texto: str) -> int | None:
    """'5 hr 10 min' / '5 h 10 min' / '5h' / '50 min' -> minutos. None si no se sabe."""
    m = re.search(r"(?:(\d+)\s*h(?:r|rs|ours?)?)?\s*(?:(\d+)\s*min)?", texto or "", re.I)
    if not m or not (m.group(1) or m.group(2)):
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def duracion_permitida(vuelo: Vuelo, max_horas) -> bool:
    if not max_horas:
        return True
    mins = minutos_duracion(vuelo.duracion)
    return mins is None or mins <= float(max_horas) * 60


def aerolinea_permitida(vuelo: Vuelo, permitidas: list[str]) -> bool:
    """Todas las aerolíneas del vuelo deben estar en la lista permitida."""
    if not permitidas:
        return True
    partes = re.split(r",\s*|\s+and\s+", vuelo.aerolineas)
    partes = [p.strip().lower() for p in partes if p.strip()]
    perm = [p.lower() for p in permitidas]
    return bool(partes) and all(any(a in p for a in perm) for p in partes)


# --------------------------------------------------------------------------- #
# URLs de Google Flights
# --------------------------------------------------------------------------- #
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _campo_bytes(num: int, data: bytes) -> bytes:
    return _varint((num << 3) | 2) + _varint(len(data)) + data


def _campo_int(num: int, valor: int) -> bytes:
    return _varint(num << 3) + _varint(valor)


def tfs_url(tramos: list[tuple[str, str, str]], adultos: int, ninos: int = 0) -> str:
    """
    URL exacta de Google Flights (parámetro tfs), con número de adultos y tipo
    de viaje fijados, sin depender de que Google entienda una frase.
    tramos: [(fecha, origen IATA, destino IATA), ...]; 1 tramo = solo ida, 2 = ida y vuelta.
    """
    info = b""
    for fecha, o, d in tramos:
        fd = (_campo_bytes(2, fecha.encode())
              + _campo_bytes(13, _campo_bytes(2, o.encode()))
              + _campo_bytes(14, _campo_bytes(2, d.encode())))
        info += _campo_bytes(3, fd)
    pasajeros = [1] * adultos + [2] * ninos          # 1 = adulto (12+), 2 = niño (2-11)
    info += _campo_bytes(8, b"".join(_varint(x) for x in pasajeros))
    info += _campo_int(9, 1)                                               # turista
    info += _campo_int(19, 1 if len(tramos) == 2 else 2)                   # ida y vuelta / solo ida
    tfs = base64.b64encode(info).decode()
    params = {"tfs": tfs, "hl": "en", "gl": "SE", "curr": "SEK"}
    return "https://www.google.com/travel/flights/search?" + urllib.parse.urlencode(params)


def texto_url(origen: str, destino: str, ida: str, vuelta: str | None,
              adultos: int, max_escalas: int, ninos: int = 0) -> str:
    """URL de respaldo con búsqueda en lenguaje natural."""
    escalas_txt = "nonstop" if max_escalas == 0 else f"{max_escalas} stop or fewer"
    pax = f"{adultos} adults" + (f" and {ninos} children" if ninos else "")
    if vuelta:
        q = (f"Flights from {origen} to {destino} on {ida} through {vuelta} "
             f"for {pax} {escalas_txt}")
    else:
        q = (f"One way flights from {origen} to {destino} on {ida} "
             f"for {pax} {escalas_txt}")
    params = {"q": q, "hl": "en", "gl": "SE", "curr": "SEK"}
    return "https://www.google.com/travel/flights?" + urllib.parse.urlencode(params)


# --------------------------------------------------------------------------- #
# Navegación
# --------------------------------------------------------------------------- #
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


LEER_TARJETAS_JS = """(sel) => {
    const out = [], vistos = new Set();
    for (const el of document.querySelectorAll(sel)) {
        const card = el.closest('li, [role="listitem"]')
                     || (el.parentElement && el.parentElement.parentElement) || el;
        if (vistos.has(card)) continue;
        vistos.add(card);
        const etiquetas = [card.getAttribute('aria-label') || '']
            .concat([...card.querySelectorAll('[aria-label]')].map(a => a.getAttribute('aria-label')));
        const principal = etiquetas.find(t => /flight with|Leaves /i.test(t || '')) || '';
        out.push({principal, texto: (card.innerText || '').replace(/\\s+/g, ' ').trim()});
    }
    return out;
}"""


@dataclass
class Busqueda:
    tipo: str
    url: str
    vuelos: list = field(default_factory=list)   # vuelos que cumplen filtros
    total_resultados: int = 0
    captura: str = ""                             # imagen JPEG en base64
    error: str = ""


def cargar_y_leer(page, url: str) -> list[dict] | None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    aceptar_cookies(page)
    try:
        page.wait_for_selector(SELECTOR, timeout=25000)
    except Exception:
        return None
    page.wait_for_timeout(2500)
    return page.evaluate(LEER_TARJETAS_JS, SELECTOR)


def buscar(page, tipo: str, url: str, url_respaldo: str, nombre: str, cfg: dict) -> Busqueda:
    log(f"Buscando '{nombre} · {tipo}'")
    b = Busqueda(tipo=tipo, url=url)
    tarjetas = cargar_y_leer(page, url)
    if not tarjetas:
        log("  sin resultados con la URL exacta; probando búsqueda por texto")
        b.url = url_respaldo
        tarjetas = cargar_y_leer(page, url_respaldo)
    try:
        b.captura = base64.b64encode(
            page.screenshot(type="jpeg", quality=55)).decode()
    except Exception:
        pass
    if not tarjetas:
        page.screenshot(path=str(BASE / "ultimo_error.png"), full_page=True)
        b.error = "No aparecieron resultados (captura en ultimo_error.png)"
        log("  " + b.error)
        return b

    debug = [f"{page.url}"]
    vuelos, vistos = [], set()
    for t in tarjetas:
        v = parse_tarjeta(t.get("principal", ""), t.get("texto", ""))
        debug.append(f"{'OK ' + str(asdict(v)) if v else 'NO LEÍDO'}\n"
                     f"  principal: {t.get('principal')}\n  texto: {t.get('texto')}")
        if not v or v.texto in vistos:
            continue
        vistos.add(v.texto)
        if (v.escalas <= cfg["max_escalas"]
                and aerolinea_permitida(v, cfg["aerolineas"])
                and duracion_permitida(v, cfg.get("max_horas_vuelo"))):
            vuelos.append(v)
    try:
        with (BASE / "debug_resultados.txt").open("a", encoding="utf-8") as f:
            f.write(f"\n===== {nombre} · {tipo} =====\n" + "\n\n".join(debug) + "\n")
    except OSError:
        pass
    vuelos.sort(key=lambda v: v.precio)
    b.vuelos, b.total_resultados = vuelos, len(tarjetas)
    log(f"  {len(tarjetas)} resultados, {len(vuelos)} cumplen los filtros"
        + (f"; más barato {sek(vuelos[0].precio)} ({vuelos[0].aerolineas})" if vuelos else ""))
    return b


@dataclass
class Opcion:
    precio: int
    tipo: str      # "ida y vuelta" o "combinada"
    detalle: str
    url: str


def revisar_grupo(page, g: dict, cfg: dict) -> dict:
    """Hace las búsquedas de un grupo y devuelve todo lo necesario para el informe."""
    O, D = cfg["aeropuerto_origen"], cfg["aeropuerto_destino"]
    # Jóvenes de 12+ años pagan como adultos en Google Flights
    o, d, esc = cfg["origen"], cfg["destino"], cfg["max_escalas"]
    n = g.get("adultos", 0) + g.get("jovenes", 0)
    k = g.get("ninos", 0)
    nombre = g["nombre"]

    # Persona que solo vuela de ida (sin "vuelta" en la configuración)
    if not g.get("vuelta"):
        b = buscar(page, "solo ida", tfs_url([(g["ida"], O, D)], n, k),
                   texto_url(o, d, g["ida"], None, n, esc, k), nombre, cfg)
        opciones = []
        if b.vuelos:
            v = b.vuelos[0]
            opciones.append(Opcion(v.precio, "solo ida",
                                   f"{v.aerolineas} · {v.salida}–{v.llegada} · "
                                   f"{v.escalas} escala/s", b.url))
        mejor = opciones[0] if opciones else None
        if mejor:
            log(f"  solo ida: {sek(mejor.precio)} · {mejor.detalle}  <-- mejor")
        return {"grupo": g, "busquedas": [b], "opciones": opciones,
                "mejor": mejor, "avisos": []}

    busquedas: list[Busqueda] = [
        buscar(page, "ida y vuelta",
               tfs_url([(g["ida"], O, D), (g["vuelta"], D, O)], n, k),
               texto_url(o, d, g["ida"], g["vuelta"], n, esc, k), nombre, cfg)
    ]
    if cfg.get("combinar_aerolineas", True):
        busquedas.append(buscar(page, "solo ida", tfs_url([(g["ida"], O, D)], n, k),
                                texto_url(o, d, g["ida"], None, n, esc, k), nombre, cfg))
        busquedas.append(buscar(page, "solo vuelta", tfs_url([(g["vuelta"], D, O)], n, k),
                                texto_url(d, o, g["vuelta"], None, n, esc, k), nombre, cfg))

    por_tipo = {b.tipo: b for b in busquedas}
    opciones: list[Opcion] = []
    iv = por_tipo["ida y vuelta"]
    if iv.vuelos:
        v = iv.vuelos[0]
        opciones.append(Opcion(v.precio, "ida y vuelta",
                               f"{v.aerolineas} · {v.salida}–{v.llegada} · "
                               f"{v.escalas} escala/s", iv.url))
    bi, bv = por_tipo.get("solo ida"), por_tipo.get("solo vuelta")
    if bi and bv and bi.vuelos and bv.vuelos:
        vi, vv = bi.vuelos[0], bv.vuelos[0]
        opciones.append(Opcion(vi.precio + vv.precio, "combinada",
                               f"Ida {vi.aerolineas} {sek(vi.precio)} + "
                               f"Vuelta {vv.aerolineas} {sek(vv.precio)}", bi.url))

    avisos = []
    if bi and bv and bi.vuelos and bv.vuelos and iv.vuelos:
        # Comprobación de sentido común: un billete de solo ida no suele costar
        # lo mismo que uno de ida y vuelta, ni la ida igual que la vuelta exactas.
        if bi.vuelos[0].precio == bv.vuelos[0].precio == iv.vuelos[0].precio:
            avisos.append("Los tres precios son idénticos: probablemente Google no "
                          "aplicó bien la búsqueda. Revisa las capturas.")
    mejor = min(opciones, key=lambda x: x.precio) if opciones else None
    for op in opciones:
        log(f"  {op.tipo}: {sek(op.precio)} · {op.detalle}"
            + ("  <-- mejor" if op is mejor else ""))
    return {"grupo": g, "busquedas": busquedas, "opciones": opciones,
            "mejor": mejor, "avisos": avisos}


# --------------------------------------------------------------------------- #
# Notificaciones
# --------------------------------------------------------------------------- #
def notificar(titulo: str, mensaje: str, cfg: dict, url: str | None = None) -> None:
    avisos = cfg.get("notificaciones", {})

    if avisos.get("escritorio", True):
        try:
            if sys.platform == "darwin":
                def esc(t: str) -> str:
                    return t.replace("\\", "\\\\").replace('"', '\\"')
                subprocess.run(
                    ["osascript", "-e",
                     f'display notification "{esc(mensaje[:250])}" '
                     f'with title "{esc(titulo)}" sound name "Glass"'],
                    check=True, timeout=15)
            else:
                from plyer import notification
                notification.notify(title=titulo, message=mensaje[:250],
                                    app_name="Vigilante de vuelos", timeout=15)
        except Exception as e:
            log(f"  (sin notificación de escritorio: {e})")

    tema = avisos.get("ntfy_tema")
    if tema:
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{tema}",
                data=mensaje.encode("utf-8"),
                headers={
                    "Title": titulo.encode("ascii", "ignore").decode().strip() or "Vuelos",
                    "Tags": "airplane",
                    **({"Click": url} if url else {}),
                },
            )
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            log(f"  (fallo ntfy: {e})")


# --------------------------------------------------------------------------- #
# Informe HTML
# --------------------------------------------------------------------------- #
def _grafico_historial(registros: list[dict]) -> str:
    puntos = [(r["fecha"], r["total"]) for r in registros if r.get("total")][-72:]
    if len(puntos) < 2:
        return "<p class='muted'>El gráfico aparecerá cuando haya al menos 2 comprobaciones.</p>"
    W, H, P = 760, 200, 34
    vals = [p[1] for p in puntos]
    lo, hi = min(vals), max(vals)
    rango = (hi - lo) or 1
    xs = [P + i * (W - 2 * P) / (len(puntos) - 1) for i in range(len(puntos))]
    ys = [H - P - (v - lo) / rango * (H - 2 * P) for v in vals]
    linea = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    area = f"{xs[0]:.1f},{H - P} " + linea + f" {xs[-1]:.1f},{H - P}"
    i_min = vals.index(lo)
    return f"""
<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="Evolución del precio total">
  <defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#2563eb" stop-opacity=".25"/>
    <stop offset="1" stop-color="#2563eb" stop-opacity="0"/></linearGradient></defs>
  <line x1="{P}" y1="{H - P}" x2="{W - P}" y2="{H - P}" stroke="#e5e7eb"/>
  <polygon points="{area}" fill="url(#g)"/>
  <polyline points="{linea}" fill="none" stroke="#2563eb" stroke-width="2.5"
            stroke-linejoin="round" stroke-linecap="round"/>
  <circle cx="{xs[i_min]:.1f}" cy="{ys[i_min]:.1f}" r="5" fill="#16a34a"/>
  <text x="{xs[i_min]:.1f}" y="{ys[i_min] + 20:.1f}" text-anchor="middle"
        class="lbl" fill="#16a34a">mín {sek(lo)}</text>
  <text x="{P}" y="{H - 10}" class="lbl">{html.escape(puntos[0][0].replace('T', ' '))}</text>
  <text x="{W - P}" y="{H - 10}" text-anchor="end" class="lbl">{html.escape(puntos[-1][0].replace('T', ' '))}</text>
  <text x="{W - P}" y="{ys[-1] - 12:.1f}" text-anchor="end" class="lbl" font-weight="600">ahora {sek(vals[-1])}</text>
</svg>"""


def generar_informe(resultados: list[dict], total: int | None, historial: dict,
                    motivos: list[str], cfg: dict) -> None:
    e = html.escape
    ahora = datetime.now().strftime("%d/%m/%Y %H:%M")
    minimo = historial.get("minimos", {}).get("total")
    estado = ("ok", "¡Bajada de precio!") if motivos and total else \
             ("neutral", "Sin bajadas relevantes") if total else \
             ("warn", "Búsqueda incompleta")

    grupos_html = []
    for r in resultados:
        g, mejor = r["grupo"], r["mejor"]
        opciones = "".join(
            f"<div class='opt {'best' if op is mejor else ''}'>"
            f"<div class='opt-tipo'>{e(op.tipo)}{' · mejor' if op is mejor else ''}</div>"
            f"<div class='opt-precio'>{sek(op.precio)}</div>"
            f"<div class='opt-pp'>{sek(round(op.precio / (g.get('adultos', 0) + g.get('jovenes', 0) + g.get('ninos', 0))))} por persona</div>"
            f"<div class='opt-det'>{e(op.detalle)}</div>"
            f"<a href='{e(op.url)}' target='_blank'>Abrir en Google Flights →</a></div>"
            for op in r["opciones"]
        ) or "<p class='muted'>No se encontraron vuelos que cumplan los filtros.</p>"

        tablas = []
        for b in r["busquedas"]:
            filas = "".join(
                f"<tr><td>{e(v.aerolineas)}</td><td>{e(v.salida)} – {e(v.llegada)}</td>"
                f"<td>{e(v.duracion)}</td>"
                f"<td>{'Directo' if v.escalas == 0 else f'{v.escalas} escala'}</td>"
                f"<td class='num'>{sek(v.precio)}</td></tr>"
                for v in b.vuelos[:6]
            ) or f"<tr><td colspan='5' class='muted'>{e(b.error or 'Ningún vuelo cumple los filtros')}</td></tr>"
            captura = (f"<details><summary>Ver captura de Google Flights</summary>"
                       f"<img src='data:image/jpeg;base64,{b.captura}' alt='captura'></details>"
                       if b.captura else "")
            tablas.append(f"""
<div class='busq'>
  <h4>{e(b.tipo.capitalize())} <span class='muted'>· {b.total_resultados} resultados,
      {len(b.vuelos)} cumplen filtros · <a href='{e(b.url)}' target='_blank'>abrir</a></span></h4>
  <table><thead><tr><th>Aerolínea</th><th>Horario</th><th>Duración</th><th>Escalas</th>
  <th class='num'>Precio</th></tr></thead><tbody>{filas}</tbody></table>{captura}
</div>""")

        avisos = "".join(f"<div class='aviso'>⚠️ {e(a)}</div>" for a in r["avisos"])
        grupos_html.append(f"""
<section class='card'>
  <div class='card-head'>
    <div><h3>{e(g['nombre'])}</h3>
      <div class='muted'>{e(g['ida'])} → {e(g.get('vuelta') or 'solo ida')} · {g.get('adultos', 0)} adulto(s){f" + {g['jovenes']} joven(es)" if g.get('jovenes') else ''}{f" + {g['ninos']} niño(s)" if g.get('ninos') else ''} ·
      {e(cfg['aeropuerto_origen'])} ⇄ {e(cfg['aeropuerto_destino'])}</div></div>
    <div class='big'>{sek(mejor.precio) if mejor else '—'}</div>
  </div>
  {avisos}
  <div class='opts'>{opciones}</div>
  {''.join(tablas)}
</section>""")

    motivos_html = "".join(f"<li>{e(m)}</li>" for m in motivos)
    doc = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vuelos Estocolmo – Barcelona · {ahora}</title>
<style>
  :root {{ --bg:#f5f7fb; --card:#fff; --txt:#0f172a; --muted:#64748b; --line:#e5e7eb;
          --blue:#2563eb; --green:#16a34a; --amber:#d97706; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--txt); }}
  .wrap {{ max-width:960px; margin:0 auto; padding:28px 18px 60px; }}
  header.hero {{ background:linear-gradient(135deg,#1e3a8a,#2563eb 55%,#0ea5e9);
         color:#fff; border-radius:20px; padding:28px; margin-bottom:22px;
         box-shadow:0 10px 30px rgba(37,99,235,.25); }}
  .hero h1 {{ margin:0 0 4px; font-size:26px; }}
  .hero .sub {{ opacity:.85; }}
  .hero .total {{ font-size:44px; font-weight:800; margin-top:14px; letter-spacing:-1px; }}
  .pill {{ display:inline-block; padding:4px 12px; border-radius:999px; font-weight:600;
          font-size:13px; background:rgba(255,255,255,.18); margin-top:10px; }}
  .pill.ok {{ background:#22c55e; }} .pill.warn {{ background:#f59e0b; }}
  .stats {{ display:flex; gap:22px; flex-wrap:wrap; margin-top:12px; opacity:.95; }}
  .hero ul {{ margin:8px 0 0; padding-left:18px; }}
  .card {{ background:var(--card); border-radius:16px; padding:20px; margin-bottom:18px;
          box-shadow:0 1px 3px rgba(15,23,42,.06),0 6px 18px rgba(15,23,42,.04); }}
  .card-head {{ display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }}
  h3 {{ margin:0; font-size:19px; }} h4 {{ margin:18px 0 8px; font-size:15px; }}
  .big {{ font-size:28px; font-weight:800; color:var(--blue); white-space:nowrap; }}
  .muted {{ color:var(--muted); font-weight:400; }}
  .opts {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:12px; margin-top:16px; }}
  .opt {{ border:1px solid var(--line); border-radius:12px; padding:14px; }}
  .opt.best {{ border:2px solid var(--green); background:#f0fdf4; }}
  .opt-tipo {{ text-transform:uppercase; font-size:12px; letter-spacing:.06em; color:var(--muted); font-weight:700; }}
  .opt.best .opt-tipo {{ color:var(--green); }}
  .opt-precio {{ font-size:24px; font-weight:800; }}
  .opt-pp {{ color:var(--muted); font-size:13px; }}
  .opt-det {{ margin:6px 0 8px; font-size:14px; }}
  a {{ color:var(--blue); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th,td {{ text-align:left; padding:8px 6px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; font-weight:600; }}
  details {{ margin-top:8px; }} summary {{ cursor:pointer; color:var(--blue); font-size:13px; }}
  details img {{ width:100%; border-radius:10px; margin-top:8px; border:1px solid var(--line); }}
  .aviso {{ background:#fffbeb; border:1px solid #fde68a; color:#92400e; padding:10px 12px;
           border-radius:10px; margin-top:12px; font-size:14px; }}
  .chart {{ width:100%; height:auto; }} .lbl {{ font-size:12px; fill:#64748b; }}
  footer {{ color:var(--muted); font-size:13px; text-align:center; margin-top:26px; }}
</style></head>
<body><div class="wrap">
<header class="hero">
  <h1>✈️ Estocolmo → Barcelona</h1>
  <div class="sub">Semana Santa 2027 · {', '.join(e(a) for a in cfg['aerolineas']) or 'todas las aerolíneas'}
    · máx. {cfg['max_escalas']} escala(s){f" · máx. {cfg['max_horas_vuelo']} h de vuelo" if cfg.get('max_horas_vuelo') else ''}</div>
  <div class="total">{sek(total)}</div>
  <div class="sub">total para todo el grupo</div>
  <span class="pill {estado[0]}">{estado[1]}</span>
  {f'<ul>{motivos_html}</ul>' if motivos_html else ''}
  <div class="stats"><div>📉 Mínimo histórico: <b>{sek(minimo)}</b></div>
    <div>🕒 Comprobado: <b>{ahora}</b></div></div>
</header>
<section class="card"><h3>Evolución del precio total</h3>{_grafico_historial(historial.get('registros', []))}</section>
{''.join(grupos_html)}
<footer>Precios leídos de Google Flights. Confírmalos siempre en la web de la aerolínea antes de comprar.<br>
Una opción "combinada" son dos reservas distintas.</footer>
</div></body></html>"""
    REPORT_PATH.write_text(doc, encoding="utf-8")
    log(f"Informe guardado en {REPORT_PATH}")


def abrir_informe() -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(REPORT_PATH)])
        elif sys.platform.startswith("win"):
            import os
            os.startfile(str(REPORT_PATH))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(REPORT_PATH)])
    except Exception as e:
        log(f"No pude abrir el informe: {e}")


# --------------------------------------------------------------------------- #
# Lógica principal
# --------------------------------------------------------------------------- #
def comprobar(cfg: dict, visible: bool = False, abrir: bool = False) -> None:
    from playwright.sync_api import sync_playwright

    historial = load_json(HISTORY_PATH, {"registros": [], "minimos": {}})
    ahora = datetime.now().isoformat(timespec="minutes")
    try:
        (BASE / "debug_resultados.txt").write_text(f"Comprobación {ahora}\n", encoding="utf-8")
    except OSError:
        pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not visible)
        context = browser.new_context(locale="en-GB", timezone_id="Europe/Stockholm",
                                      viewport={"width": 1280, "height": 1100})
        page = context.new_page()
        resultados = [revisar_grupo(page, g, cfg) for g in cfg["busquedas"]]
        browser.close()

    completo = all(r["mejor"] for r in resultados)
    dudoso = any(r["avisos"] for r in resultados)
    total = sum(r["mejor"].precio for r in resultados) if completo else None
    motivos: list[str] = []

    registro = {"fecha": ahora, "total": total,
                "grupos": {r["grupo"]["nombre"]: asdict(r["mejor"]) if r["mejor"] else None
                           for r in resultados}}
    historial["registros"].append(registro)

    if not completo:
        log("Alguna búsqueda falló o no tuvo vuelos válidos; no se compara el total.")
    elif dudoso:
        log("Precios dudosos (ver avisos en el informe); no se envía notificación.")
        registro["dudoso"] = True
    else:
        minimo_anterior = historial["minimos"].get("total")
        objetivo = cfg.get("precio_objetivo_total")
        bajada_min = cfg.get("bajada_minima_sek", 0)
        log(f"TOTAL para todo el grupo: {sek(total)} "
            f"(mínimo anterior: {sek(minimo_anterior)})")
        if minimo_anterior is None:
            motivos.append("Primer precio registrado")
        elif total <= minimo_anterior - bajada_min:
            motivos.append(f"Nuevo mínimo: baja {sek(minimo_anterior - total)}")
        if objetivo and total <= objetivo:
            motivos.append(f"Por debajo de tu objetivo de {sek(objetivo)}")
        if minimo_anterior is None or total < minimo_anterior:
            historial["minimos"] = {"total": total, "fecha": ahora}

    save_json(HISTORY_PATH, historial)
    generar_informe(resultados, total if completo else None, historial, motivos, cfg)

    if motivos:
        lineas = [f"{r['grupo']['nombre']}: {sek(r['mejor'].precio)} ({r['mejor'].tipo})"
                  for r in resultados]
        mensaje = f"Total {sek(total)} — " + "; ".join(motivos) + "\n" + "\n".join(lineas)
        notificar("✈️ Bajada de precio Estocolmo–Barcelona", mensaje, cfg,
                  resultados[0]["mejor"].url)
        log("Notificación enviada: " + "; ".join(motivos))
        abrir = abrir or cfg.get("abrir_informe_si_baja", True)
    elif completo and not dudoso:
        log("Sin bajada real; no se envía aviso.")

    if abrir:
        abrir_informe()


def main() -> None:
    ap = argparse.ArgumentParser(description="Vigilante de precios de vuelos")
    ap.add_argument("--loop", action="store_true", help="comprobar cada hora sin parar")
    ap.add_argument("--visible", action="store_true", help="mostrar el navegador")
    ap.add_argument("--abrir", action="store_true", help="abrir informe.html al terminar")
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
            comprobar(cfg, visible=args.visible, abrir=args.abrir or args.visible)
        except Exception as e:
            log(f"ERROR general: {e}")
        if not args.loop:
            break
        log(f"Próxima comprobación en {intervalo // 60} minutos.")
        time.sleep(intervalo)


if __name__ == "__main__":
    main()
