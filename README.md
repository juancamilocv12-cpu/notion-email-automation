# Zoho Mail desarrollo a Notion

Automatización en Python para:
- Leer correos nuevos de Zoho Mail
- Crear tareas en Notion
- Enviar auto-respuestas condicionales
- Enviar alertas de pendientes >24h
- Etiquetar tareas creadas desde correo con `Correo`
- Sincronizar calendario en ambos sentidos: Zoho Calendar ↔ Notion

## Requisitos
- Python 3.10+
- `requests`
- `python-dotenv`

## Instalación
```bash
pip install requests python-dotenv
```

## Configuración
1. Copia `.env.example` a `.env`
2. Completa tus credenciales reales.

### Variables para calendario (opcionales)
- `SYNC_ZOHO_CALENDAR_TO_NOTION=true` habilita Zoho -> Notion.
- `SYNC_NOTION_CALENDAR_TO_ZOHO=true` habilita Notion -> Zoho.
- `NOTION_CALENDAR_DATABASE_ID` permite usar una base de Notion distinta a `NOTION_DATABASE_ID`.
- `ZOHO_CALENDAR_UID` fija un calendario específico de Zoho (si se omite, usa el primero disponible).
- `ZOHO_CALENDAR_TIMEZONE` zona horaria para consultas (ej: `America/Bogota`).
- `ZOHO_CALENDAR_DAYS_PAST` y `ZOHO_CALENDAR_DAYS_AHEAD` definen la ventana de sincronización.

### Desactivar Zoho Calendar
Si no quieres sincronizar calendario, configura en `.env`:

```dotenv
SYNC_ZOHO_CALENDAR_TO_NOTION=false
SYNC_NOTION_CALENDAR_TO_ZOHO=false
```

### Scopes de Zoho requeridos para calendario
Al generar el `authorization code`, incluye al menos:
- `ZohoCalendar.calendar.READ`
- `ZohoCalendar.event.READ`
- `ZohoCalendar.event.CREATE`
- `ZohoCalendar.event.UPDATE`

Si prefieres, puedes usar `ZohoCalendar.event.ALL` para evitar scopes separados.

## Ejecución manual
```bash
python main.py
```

## Renovar token de Zoho
```bash
python refresh_zoho_token.py --code "1000...."
```

También acepta URL callback completa en `--code`.

## Ejecución automática (cron)
```bash
*/5 * * * * cd /ruta/proyecto && /usr/bin/python3 main.py >> cron.log 2>&1
```

En macOS, para instalarlo en tu `crontab` actual:
```bash
(crontab -l 2>/dev/null; echo "*/5 * * * * cd /ruta/proyecto && /usr/bin/python3 main.py >> cron.log 2>&1") | crontab -
```
