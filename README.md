# Zoho Mail desarrollo a Notion

Automatización en Python para:
- Leer correos nuevos de Zoho Mail
- Crear tareas en Notion
- Enviar auto-respuestas condicionales
- Enviar alertas de pendientes >24h
- Etiquetar tareas creadas desde correo con `Correo`

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
