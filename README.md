# DB Size Ranking para Home Assistant

Integración que calcula qué entidades ocupan más espacio en la base de datos del
recorder, con dos rankings:

- **Diario**: lo escrito en las últimas 24 h (o desde medianoche, configurable).
- **Total**: todo lo que hay ahora mismo en la base de datos.

Funciona con **SQLite** (la base por defecto), **MariaDB/MySQL** y **PostgreSQL**.
Requiere Home Assistant 2025.2 o posterior.

## Instalación

### HACS (recomendado)
1. HACS → ⋮ → **Repositorios personalizados**.
2. URL: `https://github.com/imartinez82/ha-db-size-ranking`, tipo **Integración**.
3. Busca **DB Size Ranking**, descárgala y reinicia Home Assistant.
4. Ajustes → Dispositivos y servicios → Añadir integración → **DB Size Ranking**.

[![Abrir en HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=imartinez82&repository=ha-db-size-ranking&category=integration)

### Manual (sin HACS)
1. Copia la carpeta `custom_components/db_size_ranking` dentro de la carpeta
   `config/custom_components/` de tu Home Assistant (créala si no existe).
2. Reinicia Home Assistant.
3. Ajustes → Dispositivos y servicios → Añadir integración → **DB Size Ranking**.

## Qué crea

| Entidad | Estado | Atributos principales |
|---|---|---|
| `sensor.db_size_ranking_ranking_total` | MiB atribuidos a entidades (histórico) | `ranking`, `top_entity`, `orphan_count` |
| `sensor.db_size_ranking_ranking_diario` | MiB escritos en el periodo diario | `ranking`, `top_entity`, `since` |
| `sensor.db_size_ranking_ranking_dispositivos_total` | MiB atribuidos a dispositivos (histórico) | `ranking` (dispositivos), `top_device` |
| `sensor.db_size_ranking_ranking_dispositivos_diario` | MiB escritos por dispositivos en el periodo diario | `ranking` (dispositivos), `top_device` |
| `sensor.db_size_ranking_tamano_base_de_datos` | Tamaño total de la base de datos | `tables_mb` (tamaño de cada tabla) |

> Los `entity_id` exactos dependen del idioma de tu instalación; búscalos por
> "DB Size Ranking" en Ajustes → Entidades. Con HA en inglés son
> `sensor.db_size_ranking_total_ranking`, `..._daily_ranking`,
> `..._device_ranking_total`, `..._device_ranking_daily` y `..._database_size`.

Cada elemento del atributo `ranking` contiene:

- `rank`, `entity_id`, `name`
- `bytes`, `mb`, `pct` (% sobre el total del ranking) y `pct_db` (% sobre la base de datos)
- `states` (filas de estado), `attribute_rows`, `statistics`, `statistics_short_term`
- `bytes_states`, `bytes_attributes`, `bytes_statistics` (desglose)
- `exists`: `false` si la entidad ya no existe en HA → datos huérfanos que puedes borrar
- `external_statistic`: estadísticas importadas que no son una entidad

El atributo `ranking` **no se guarda en la base de datos** (no tendría sentido que
el sensor que mide el espacio se pusiera a ocupar espacio).

### Ranking de dispositivos

Suma lo que ocupan todas las entidades de cada dispositivo. Cada elemento de su
atributo `ranking` contiene:

- `rank`, `device_id`, `name` (el nombre que le hayas puesto) y `area`
- `bytes`, `mb`, `pct`, `pct_db`
- `states`, `statistics` y el desglose `bytes_states`, `bytes_attributes`, `bytes_statistics`
- `entity_count` y `entities`: sus 10 entidades que más ocupan (`entity_id`, `name`, `mb`)

Las entidades que no pertenecen a ningún dispositivo (plantillas, `input_*`,
huérfanas…) no entran en este ranking; su total aparece en los atributos
`no_device_mb` y `no_device_entities`.

Tarjeta Markdown de ejemplo:

```yaml
type: markdown
content: |
  {% set s = 'sensor.db_size_ranking_ranking_dispositivos_total' %}
  | # | Dispositivo | Área | MiB | % |
  |---|---|---|---|---|
  {% for d in state_attr(s, 'ranking')[:15] -%}
  | {{ d.rank }} | {{ d.name }} | {{ d.area or '' }} | {{ d.mb }} | {{ d.pct }} |
  {% endfor %}
```

## Cómo se calcula

- **states**: nº de filas × coste por fila + longitud del valor.
- **state_attributes**: las filas de atributos que usa cada entidad (si varias
  entidades comparten una, se reparte). En el diario sólo cuentan las creadas en el periodo.
- **statistics / statistics_short_term**: nº de filas × bytes por fila.

Si el motor permite leer el tamaño real de cada tabla (dbstat en SQLite,
`information_schema` en MariaDB, `pg_total_relation_size` en PostgreSQL), los
valores se reescalan para que cuadren con el tamaño real, índices incluidos
(`calibrated: true`). Si no, son estimaciones razonables.

Las consultas corren en los hilos de base de datos del recorder, nunca bloquean
Home Assistant. Como referencia, una base SQLite de 2 millones de estados (240 MB)
tarda unos 12 s en el ranking total y ~1 s en el diario.

## Opciones

Ajustes → Dispositivos y servicios → DB Size Ranking → Configurar:

- Número de entidades del ranking (por defecto 25)
- Periodo diario: últimas 24 h / desde medianoche
- Intervalo del ranking diario (por defecto 60 min)
- Intervalo del ranking total (por defecto 6 h)

## Servicios

- `db_size_ranking.refresh` — recalcula ahora (`period: daily|total`, opcional).
- `db_size_ranking.get_ranking` — devuelve el ranking como respuesta, con el
  `top_n` que quieras. Pruébalo en Herramientas para desarrolladores → Acciones:

```yaml
action: db_size_ranking.get_ranking
data:
  period: total
  top_n: 100
```

## Tarjeta para el panel

Tarjeta **Markdown** (cambia el `entity_id` si el tuyo es distinto):

```yaml
type: markdown
title: Lo que más ocupa en la base de datos
content: >
  {% set s = 'sensor.db_size_ranking_ranking_total' %}
  **Total:** {{ states(s) }} MiB · BD: {{ states('sensor.db_size_ranking_tamano_base_de_datos') }} MiB

  | # | Entidad | MiB | % | Estados |
  |--:|:--|--:|--:|--:|
  {% for e in (state_attr(s, 'ranking') or [])[:15] -%}
  | {{ e.rank }} | {{ e.name or e.entity_id }}{{ ' ⚠️' if not e.exists }} | {{ e.mb }} | {{ e.pct }} | {{ e.states }} |
  {% endfor %}

  ⚠️ = entidad que ya no existe
```

Para el diario, duplica la tarjeta con `sensor.db_size_ranking_ranking_diario`.

## Botones de borrado

La integración crea dos botones (en la página del dispositivo **DB Size Ranking**):

- **Borrar historial del top 50**: borra el historial (estados y atributos) de
  las 50 entidades que más ocupan según el ranking total. Las **estadísticas a
  largo plazo se conservan**, así que el panel de Energía y las gráficas de medias
  no se pierden. Al terminar aparece una notificación con la lista de lo borrado,
  y el ranking se recalcula al minuto.
- **Compactar base de datos**: ejecuta `recorder.purge` con `repack: true` para
  que el archivo encoja de verdad (en SQLite, sin esto no baja de tamaño).

El borrado no se puede deshacer y el botón no pide confirmación. Para tenerlo en
el panel con confirmación:

```yaml
type: button
name: Borrar historial top 50
icon: mdi:database-remove
entity: button.db_size_ranking_borrar_historial_del_top_50
tap_action:
  action: toggle
  confirmation:
    text: ¿Borrar el historial de las 50 entidades que más ocupan?
```

También como acción, con el número que quieras:

```yaml
action: db_size_ranking.purge_top
data:
  top_n: 20
```

Ojo: borrar no impide que vuelvan a llenarse. Para eso, exclúyelas del recorder
(siguiente apartado).

## Qué hacer con el resultado

Cuando veas una entidad que ocupa mucho y no necesitas su historial, exclúyela
del recorder en `configuration.yaml`:

```yaml
recorder:
  exclude:
    entities:
      - sensor.la_que_mas_ocupa
```

Y para borrar lo que ya tiene guardado (o los datos de entidades huérfanas):

```yaml
action: recorder.purge_entities
data:
  entity_id: sensor.la_que_mas_ocupa
```

En SQLite, el archivo no encoge hasta que se ejecuta `recorder.purge` con `repack: true`.

## Desarrollo

```bash
pip install pytest-homeassistant-custom-component
pytest
```
