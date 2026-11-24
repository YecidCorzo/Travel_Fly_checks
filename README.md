# ✈️ Travel_Fly_checks — Vigilante de vuelos Estocolmo → Barcelona

Comprueba cada hora el precio de los vuelos de Semana Santa 2027 en Google Flights (con Playwright) y te avisa **solo cuando el precio baja de verdad**.

**Qué vigila (por defecto):**

| Búsqueda | Ida | Vuelta | Personas |
|---|---|---|---|
| Grupo | 26 mar 2027 | 1 abr 2027 | 3 adultos |
| Cuarta persona | 26 mar 2027 | 4 abr 2027 | 1 adulto |

- Máximo **1 escala**.
- Solo **SAS, Norwegian y Lufthansa** (todas las aerolíneas del vuelo deben estar en la lista).
- Suma el precio más barato de cada búsqueda y te da el **total para las 4 personas**, en SEK.

**Cuándo avisa:**
- Cuando el total marca un **nuevo mínimo** y baja al menos `bajada_minima_sek` (200 SEK por defecto). Así no te llegan avisos por cambios de 10 coronas.
- Cuando el total queda **por debajo de tu precio objetivo**, si lo pones.
- Todo se guarda en `historial_precios.json`, así puedes ver si una "oferta" de verdad es más barata que lo que ya había.

## 1. Instalación

Necesitas **Python 3.10 o más nuevo**.

```bash
git clone https://github.com/YecidCorzo/Travel_Fly_checks.git
cd Travel_Fly_checks
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
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
| `busquedas` | Fechas y número de adultos de cada búsqueda |
| `aerolineas` | Aerolíneas permitidas (deja `[]` para aceptar cualquiera) |
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
- El filtro de aerolíneas se aplica al vuelo de ida que muestra Google Flights. La vuelta se elige al reservar.
- Si Google cambia su página y deja de encontrar resultados, se guarda una captura en `ultimo_error.png`. Revísala o ejecuta con `--visible` para ver qué pasa.
- Consulta con moderación: una vez por hora está bien. Comprobar con más frecuencia puede hacer que Google muestre captchas.
