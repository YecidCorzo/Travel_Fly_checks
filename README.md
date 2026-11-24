# ✈️ Travel_Fly_checks — Vigilante de vuelos Estocolmo → Barcelona

Comprueba cada hora el precio de los vuelos de Semana Santa 2027 en Google Flights (con Playwright) y te avisa **solo cuando el precio baja de verdad**.

**Qué vigila (por defecto):**

| Búsqueda | Ida | Vuelta | Personas |
|---|---|---|---|
| Grupo | 26 mar 2027 | 1 abr 2027 | 3 adultos |
| Cuarta persona | 26 mar 2027 | 4 abr 2027 | 1 adulto |
| Quinta persona | 26 mar 2027 | — (solo ida) | 1 adulto |

- Máximo **1 escala**.
- Solo **SAS, Norwegian y Lufthansa** (todas las aerolíneas del vuelo deben estar en la lista).
- **Combina aerolíneas:** además del billete de ida y vuelta, busca solo ida y solo vuelta por separado (p. ej. ida con Norwegian + vuelta con SAS) y se queda con lo más barato. Dentro de un vuelo con escala también acepta mezclas, como SAS + Lufthansa.
- Suma el precio más barato de cada búsqueda y te da el **total para todo el grupo**, en SEK.

**Cuándo avisa:**
- Cuando el total marca un **nuevo mínimo** y baja al menos `bajada_minima_sek` (200 SEK por defecto). Así no te llegan avisos por cambios de 10 coronas.
- Cuando el total queda **por debajo de tu precio objetivo**, si lo pones.
- Todo se guarda en `historial_precios.json`, así puedes ver si una "oferta" de verdad es más barata que lo que ya había.

## Informe HTML

Cada comprobación crea **`informe.html`** con:
- el total del grupo y el mínimo histórico,
- un gráfico de cómo evoluciona el precio,
- para cada grupo, la opción más barata (ida y vuelta o combinada) y el precio por persona,
- los vuelos que cumplen los filtros y una **captura de Google Flights** de cada búsqueda, para comprobar los precios.

Ábrelo con `open informe.html` (Mac), o ejecuta `python vigilante.py --abrir`. Se abre solo cuando hay una bajada de precio.

Si los precios parecen raros (por ejemplo, idénticos en todas las búsquedas), el informe lo marca con ⚠️ y no se envía aviso. Todo lo que el programa lee de Google se guarda en `debug_resultados.txt`.

## 1. Instalación

Necesitas **Python 3.10 o más nuevo**.

```bash
git clone https://github.com/YecidCorzo/Travel_Fly_checks.git
cd Travel_Fly_checks
python3 -m venv .venv            # Windows: python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

## 2. Configuración

Copia el ejemplo y edítalo:

```bash
cp config.example.json config.json      # Windows: copy config.example.json config.json
```

| Campo | Qué hace |
|---|---|
| `busquedas` | Fechas y número de adultos de cada búsqueda. Con `"vuelta": null` busca solo ida |
| `aerolineas` | Aerolíneas permitidas (deja `[]` para aceptar cualquiera) |
| `combinar_aerolineas` | `true` = compara también dos billetes de solo ida con aerolíneas distintas |
| `aeropuerto_origen` / `aeropuerto_destino` | Códigos IATA (ARN = Arlanda, BCN = Barcelona) |
| `max_escalas` | `1` = directo o una escala |
| `precio_objetivo_total` | Ej. `12000`: avisa siempre que el total esté por debajo |
| `bajada_minima_sek` | Cuánto tiene que bajar para contar como nuevo mínimo |
| `notificaciones.escritorio` | Notificación en tu ordenador |
| `notificaciones.ntfy_tema` | Avisos al móvil (ver abajo) |

### Avisos en el móvil (opcional, gratis)
1. Instala la app **ntfy** (Android/iPhone).
2. Inventa un nombre de tema difícil de adivinar, p. ej. `yecid-vuelos-bcn-8372`, y suscríbete a él en la app.
3. Pon ese nombre en `"ntfy_tema"` dentro de `config.json`.

Prueba los avisos con:
```bash
python vigilante.py --test-aviso
```

## 3. Primera prueba

```bash
python vigilante.py --visible
```
Se abre el navegador, hace las dos búsquedas y escribe el resultado en pantalla y en `vigilante.log`.

## 4. Que se ejecute cada hora

**Opción fácil (cualquier sistema):** déjalo abierto en una terminal:
```bash
python vigilante.py --loop
```

**Windows (Programador de tareas):**
```powershell
powershell -ExecutionPolicy Bypass -File .\instalar_tarea_windows.ps1
```
Para quitarla: `Unregister-ScheduledTask -TaskName VigilanteVuelosBarcelona`

**macOS / Linux (cron):** ejecuta `crontab -e` y añade una línea con la ruta de tu carpeta:
```
0 * * * * cd /ruta/a/Travel_Fly_checks && .venv/bin/python vigilante.py >> cron.log 2>&1
```
En macOS puede que tengas que dar a la Terminal permiso de "Acceso total al disco" para que cron funcione.

## Notas
- El ordenador tiene que estar encendido (no en reposo) para que se ejecute.
- Los precios de Google Flights son ida y vuelta, total para los adultos de cada búsqueda. Confírmalo siempre en la web de la aerolínea antes de comprar.
- En el billete de ida y vuelta, el filtro de aerolíneas se aplica a la ida que muestra Google Flights; la vuelta se elige al reservar. En la opción combinada se filtran las dos.
- Una opción combinada son **dos reservas distintas**: si un vuelo se cancela o cambia, la otra aerolínea no está obligada a ayudarte. Cada búsqueda hace hasta 3 consultas, así que una comprobación completa tarda unos minutos.
- Si Google cambia su página y deja de encontrar resultados, se guarda una captura en `ultimo_error.png`. Revísala o ejecuta con `--visible` para ver qué pasa.
- Consulta con moderación: una vez por hora está bien. Comprobar con más frecuencia puede hacer que Google muestre captchas.
