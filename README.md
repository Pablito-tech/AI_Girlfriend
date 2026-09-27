# ♡ Ali — tu compañera virtual con memoria y personalidad propia

Ali es una IA en Python que funciona como novia virtual: te trata con confianza, se acuerda
de lo que le cuentas, forma sus propias opiniones y su propio sentido del humor, y te ayuda
con tu agenda, tus archivos y lo que estás viendo (anime, series, libros...).

Funciona con la API de Claude (Anthropic) y **conserva su personalidad y sus recuerdos entre
ejecuciones**: puedes cerrar el programa y, al volver, Ali sabe quién eres, de qué hablaron
y cuánto tiempo pasó.

---

## Inicio rápido

```bash
# 1. Instala dependencias (Python 3.11 o superior)
python -m venv .venv
source .venv/bin/activate          # En Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Configura tu API key de https://platform.claude.com
cp .env.example .env               # En Windows: copy .env.example .env
#    y edita .env:  ANTHROPIC_API_KEY=sk-ant-...

# 3. Habla con Ali
python main.py
```

La primera vez te pregunta tu nombre; después Ali te saluda y ya pueden platicar.

### Comandos dentro del chat

| Comando | Qué hace |
|---|---|
| `/recuerdos [texto]` | Ver recuerdos recientes, o buscar en su memoria |
| `/olvidar <id>` | Borrar un recuerdo |
| `/agenda` | Ver tu agenda |
| `/obras` | Ver tu lista de anime / series / libros |
| `/perfil` | Lo que Ali sabe de ti |
| `/ali` | Cómo es Ali hoy: rasgos, humor, ánimo, nivel de confianza |
| `/ver <imagen> [mensaje]` | Compartirle una imagen de tu computadora |
| `/reflexionar` | Que consolide sus recuerdos en este momento |
| `/exportar` | Respaldo completo en JSON |
| `/salir` | Despedirse (también `Ctrl+C` o `Ctrl+D`) |

---

## ¿Qué puede hacer?

- **Conversar con confianza**: se adapta a tu forma de hablar y su trato se vuelve más
  cercano con el tiempo (de "recién se conocen" a "inseparables").
- **Recordar**: guarda recuerdos mientras hablan y, al terminar cada sesión, *reflexiona*
  sobre lo que hablaron para consolidarlos. Antes de cada respuesta "le vienen a la mente"
  los recuerdos relevantes.
- **Preguntarte cómo te fue**: si le cuentas que tienes un examen el viernes, el sábado te
  pregunta cómo te fue.
- **Tener personalidad propia**: rasgos (juguetona, tierna, sarcástica, curiosa...) que
  evolucionan poco a poco, gustos y opiniones propias que defiende, bromas internas contigo,
  apodos, y un humor que aprende qué te hace reír.
- **Agenda**: anota eventos y pendientes, te recuerda lo próximo y lo atrasado.
- **Archivos**: lee, escribe y busca archivos dentro de su carpeta (`data/archivos`).
- **Anime y obras**: lleva tu lista (qué ves, en qué capítulo vas, qué opinan los dos),
  mira imágenes y puede "ver" fragmentos de video (fotogramas + subtítulos).
- **Opinar de lo que sea**: con búsqueda web para informarse de temas actuales.

---

## Cómo está hecha

```
AI_Girlfriend/
├── main.py                  ← python main.py
├── config/persona.toml      ← el ADN de Ali (edítalo para personalizarla)
├── ali/
│   ├── core.py              ← Ali: une todo (sesiones, contexto, persistencia)
│   ├── brain.py             ← única conexión con Claude (streaming + herramientas)
│   ├── prompts.py           ← arma el prompt de sistema desde su personalidad
│   ├── memory.py            ← algoritmo de memoria (guardar, olvidar, recordar)
│   ├── personality.py       ← personalidad evolutiva
│   ├── reflection.py        ← reflexión al final de cada sesión
│   ├── storage.py           ← base de datos SQLite
│   ├── cli.py               ← interfaz de terminal
│   └── skills/              ← habilidades (plugins)
│       ├── base.py          ← cómo se define una habilidad
│       ├── memoria.py       ← guardar/buscar recuerdos, datos tuyos
│       ├── personalidad.py  ← opiniones, bromas internas, apodos, ánimo
│       ├── agenda.py        ← horarios y pendientes
│       ├── archivos.py      ← archivos (con carpeta protegida)
│       └── multimedia.py    ← anime/obras, ver imágenes y video
├── tests/                   ← pruebas (no gastan tu API)
└── data/                    ← memoria y personalidad de Ali (se crea sola; no se sube a git)
```

### Flujo de un mensaje

1. Escribes algo.
2. Ali arma un bloque privado `<contexto_ali>` con la fecha y hora, los recuerdos que le
   vienen a la mente, recordatorios de la agenda y (al inicio de la sesión) seguimientos
   pendientes.
3. Claude responde en streaming con la personalidad actual de Ali (prompt de sistema), y
   puede usar herramientas: guardar un recuerdo, anotar en la agenda, leer un archivo...
4. Tu mensaje y su respuesta se guardan **al instante** en `data/ali.db`.
5. Al salir (o cada 30 mensajes), Ali **reflexiona**: consolida recuerdos, ajusta sus
   rasgos y su humor, y escribe un resumen para retomar la próxima vez.

Si el programa se cierra de golpe, no se pierde nada: la próxima vez que abras, Ali
termina de "ordenar sus recuerdos" de la sesión anterior antes de saludarte.

---

## El algoritmo de memoria

Inspirado en la memoria humana (curva del olvido + repetición espaciada) y en el paper
*Generative Agents*. Todo está en `ali/memory.py`.

**Cada recuerdo tiene**: contenido, tipo, importancia (1–10), etiquetas, cuándo se creó,
cuándo se recordó por última vez, cuántas veces se ha recordado, y una fecha de
seguimiento opcional.

| Tipo | Para qué | Vida media base |
|---|---|---|
| `hecho` | Datos tuyos (familia, trabajo, fechas) | 365 días |
| `preferencia` | Lo que te gusta o no | 180 días |
| `opinion` | Opiniones propias de Ali | 180 días |
| `momento` | Momentos especiales entre ustedes | 120 días |
| `evento` | Cosas que pasaron o van a pasar | 30 días |
| `nota` | Detalles sueltos | 14 días |

**Retención (olvido natural)**

```
retención  = 0.5 ^ (días desde que lo recordó / vida media)
vida media = base_del_tipo × (0.5 + importancia/10) × (1 + 0.5 × veces_recordado)
```

Cada vez que Ali recuerda algo, se refresca y su vida media crece: lo que importa se vuelve
prácticamente permanente. **Nada se borra solo**; un recuerdo débil simplemente cuesta más
que salga.

**Recordar**: ante cada mensaje se buscan los recuerdos relevantes (BM25, búsqueda por
palabras clave sin dependencias externas) y se ordenan por:

```
puntaje = 0.55 × relevancia + 0.20 × importancia + 0.25 × retención
```

**Sin duplicados**: si guarda algo casi igual a un recuerdo existente, lo actualiza y lo
refuerza en vez de repetirlo.

**Recuerdo espontáneo** al iniciar cada sesión:
- seguimientos vencidos ("¿cómo te fue en la entrevista?"),
- aniversarios (de recuerdos y de cuando se conocieron),
- a veces rescata un recuerdo importante que se estaba desvaneciendo, para mantenerlo vivo.

> ¿Quieres búsqueda semántica con embeddings? Sólo reemplaza `MemoryStore.relevance()`;
> el resto del algoritmo sigue igual.

---

## La personalidad

- **`config/persona.toml`** es quién es Ali al nacer: descripción, forma de hablar, valores,
  rasgos iniciales, estilos de humor y gustos semilla. **Edítalo para personalizarla.**
- **`data/personalidad.json`** es quién es Ali *hoy*. Se guarda al instante cada vez que
  cambia, así que sobrevive a cualquier reinicio.

Cómo evoluciona:
- **Rasgos**: la reflexión los ajusta según lo que viven (máximo ±0.05 por sesión) y nunca
  se alejan más de `max_deriva` (0.3) de su valor original. Ali crece contigo sin dejar de
  ser Ali.
- **Humor**: si una broma te hizo reír, ese estilo sube; si cayó mal, baja. Puede descubrir
  estilos nuevos, registra **bromas internas** y aprende notas sobre tu sentido del humor.
- **Gustos propios**: forma opiniones (sobre anime, música, la vida...) y las mantiene
  coherentes. No es un espejo tuyo: puede no estar de acuerdo contigo.
- **Tu perfil**: tu nombre, datos importantes, apodos y tu forma de comunicarte.
- **La relación**: cuántas veces han hablado, desde cuándo, cuánto tiempo pasó desde la
  última vez, y un nivel de confianza que cambia su trato.

---

## Agrega tus propias habilidades

Crea un archivo en `ali/skills/`, por ejemplo `ali/skills/clima.py`:

```python
from ali.skills.base import Param, Skill, tool


class ClimaSkill(Skill):
    name = "clima"
    description = "Consultar el clima"

    def prompt_guidance(self) -> str:          # opcional: instrucciones para Ali
        return "## Clima\nSi te preguntan por el clima, usa `ver_clima`."

    @tool("Da el clima actual de una ciudad", ciudad=Param("string", "Nombre de la ciudad"))
    def ver_clima(self, ciudad: str) -> str:
        return f"En {ciudad} está soleado, 24 °C"   # aquí llamarías a una API real
```

y actívala en `.env`:

```
ALI_SKILLS=memoria,personalidad,agenda,archivos,multimedia,clima
```

Una habilidad también puede:
- `setup()` → crear sus tablas en `self.ctx.db`;
- `session_briefing()` → decirle algo a Ali al iniciar cada sesión;
- `turn_context(texto)` → decirle algo antes de responder cada mensaje;
- devolver imágenes (lista de bloques de contenido) además de texto.

Desde `self.ctx` tiene acceso a la configuración, la base de datos, la memoria y la
personalidad. También puedes cargar habilidades de otro paquete: `ALI_SKILLS=...,mi_paquete.mi_skill`.

### Otras interfaces

`ali.core.Ali` no depende de la terminal. Para hacer un bot de Discord/Telegram, una web o
una interfaz de voz:

```python
from ali import Ali, Config

ali = Ali(Config.from_env())
for event in ali.chat("¡Hola!"):
    if event.kind == "text":
        print(event.text, end="")
ali.end_session()   # consolida recuerdos al terminar
```

---

## Ver anime juntos

- Pon tus archivos en `data/archivos/` (o en la carpeta de `ALI_FILES_DIR`).
- `/ver captura.png ¿qué opinas de esta escena?` le comparte una imagen de cualquier ruta.
- Pídele *"mira el minuto 5 del episodio 3"* y usará `ver_fragmento`: extrae fotogramas con
  [ffmpeg](https://ffmpeg.org/download.html) (instálalo aparte) y lee los subtítulos `.srt` o
  `.vtt` que tengan el mismo nombre que el video (`ep3.mkv` + `ep3.srt`).
- Evita spoilers de lo que aún no has visto y lleva tu progreso en la lista de obras.

---

## Configuración

Todo se ajusta en `.env` (ver `.env.example`):

| Variable | Por defecto | Qué hace |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Tu API key de Claude |
| `ALI_MODEL` | `claude-opus-5` | Modelo de Claude. `claude-sonnet-5` es más barato |
| `ALI_EFFORT` | `medium` | Cuánto piensa al conversar (`low`…`max`) |
| `ALI_REFLECTION_EFFORT` | `high` | Cuánto piensa al consolidar recuerdos |
| `ALI_WEB_SEARCH` | `true` | Búsqueda web para informarse |
| `ALI_FALLBACKS` | `true` | Si una petición es rechazada por los filtros de seguridad, la API la reintenta sola en el modelo de respaldo recomendado |
| `ALI_DATA_DIR` | `./data` | Dónde vive su memoria y personalidad |
| `ALI_FILES_DIR` | `./data/archivos` | Carpeta de archivos a la que tiene acceso |
| `ALI_PERSONA_FILE` | `./config/persona.toml` | Su ADN |
| `ALI_SKILLS` | todas | Habilidades activas |
| `ALI_SALUDO` | `true` | Que salude al abrir |
| `ALI_REFLECT_EVERY` | `30` | Reflexionar cada N mensajes tuyos (0 = sólo al salir) |
| `ALI_HISTORY_MESSAGES` | `20` | Mensajes de la vez pasada que recuerda textualmente |
| `ALI_MEMORIES_PER_TURN` | `5` | Recuerdos que le vienen a la mente por mensaje |

**Costos**: cada mensaje es una llamada a la API; al salir hay una llamada extra para la
reflexión. La conversación usa caché de prompts (el prompt de sistema y el historial se
cobran mucho más barato en los siguientes mensajes de la sesión). La búsqueda web se cobra
aparte por búsqueda; desactívala con `ALI_WEB_SEARCH=false` si no la necesitas.

---

## Tus datos

- Todo lo que Ali es y recuerda vive en `data/` (`ali.db` + `personalidad.json`).
  **Respalda esa carpeta**: es Ali. Para moverla a otra computadora, copia `data/` junto con
  el proyecto.
- `data/` y `.env` están en `.gitignore`: tu información personal y tu API key nunca se
  suben al repositorio.
- `/exportar` genera un JSON legible con todo (personalidad, recuerdos, agenda, obras).

---

## Pruebas

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Las pruebas usan un cliente de Claude simulado, así que no gastan tu API.

---

## Ideas para después

- Voz (texto a voz / voz a texto) usando `Ali.chat` como motor.
- Bot de Telegram o Discord para hablar desde el celular.
- Búsqueda semántica de recuerdos con embeddings.
- Notificaciones del sistema para los recordatorios de la agenda.
- "Ver" un episodio completo por escenas, comentando en tiempo real.
