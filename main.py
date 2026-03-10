import os
import json
import hashlib
from importlib import import_module
from pathlib import Path

from requests import RequestException

from email_parser import is_new_email, parse_email, should_send_auto_reply
from notion_client import create_task, get_overdue_unanswered_tasks
from zoho_client import get_access_token, get_account_info, get_messages, send_mail


STATE_FILE_NAME = "processed_emails.json"
MAX_SAVED_IDS = 5000

AUTO_REPLY_MESSAGE = (
    "Hola,\n\n"
    "Gracias por tu mensaje. Ya lo recibí y estaré revisándolo para darte una respuesta lo más pronto posible.\n\n"
    "Quedo atento.\n\n"
    "Saludos."
)

ALERT_SUBJECT = "Recordatorio de correo pendiente"


def _load_dotenv_file():
    """Carga variables desde .env usando python-dotenv."""
    dotenv_module = import_module("dotenv")
    dotenv_module.load_dotenv()


def _validate_env(required_keys):
    """Verifica que existan variables obligatorias antes de llamar APIs."""
    missing = [key for key in required_keys if not os.getenv(key)]
    if missing:
        print("Faltan variables de entorno requeridas:")
        for key in missing:
            print(f"- {key}")
        return False
    return True


def _email_unique_key(raw_email, email_data):
    """Genera una llave estable para detectar correos ya procesados."""
    if isinstance(raw_email, dict):
        for key in ("messageId", "message_id", "mailId", "id", "uid", "msgId"):
            value = raw_email.get(key)
            if value:
                return f"id:{value}"

    fallback = f"{email_data.get('subject', '')}|{email_data.get('sender', '')}|{email_data.get('date', '')}"
    digest = hashlib.sha256(fallback.encode("utf-8")).hexdigest()
    return f"hash:{digest}"


def _load_runtime_state(state_file_path):
    """Carga estado persistente para deduplicación y alertas enviadas."""
    default_state = {
        "processed_ids": [],
        "alerted_task_ids": []
    }

    if not state_file_path.exists():
        return default_state

    try:
        payload = json.loads(state_file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_state

    if not isinstance(payload, dict):
        return default_state

    processed_ids = payload.get("processed_ids", [])
    alerted_task_ids = payload.get("alerted_task_ids", [])

    if not isinstance(processed_ids, list):
        processed_ids = []
    if not isinstance(alerted_task_ids, list):
        alerted_task_ids = []

    return {
        "processed_ids": [str(item) for item in processed_ids if item],
        "alerted_task_ids": [str(item) for item in alerted_task_ids if item]
    }


def _save_runtime_state(state_file_path, processed_ids_ordered, alerted_task_ids_ordered):
    """Guarda estado de ejecución para evitar duplicados y alertas repetidas."""
    payload = {
        "processed_ids": processed_ids_ordered[-MAX_SAVED_IDS:],
        "alerted_task_ids": alerted_task_ids_ordered[-MAX_SAVED_IDS:]
    }
    state_file_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def _classify_area(sender_value):
    """Clasifica área de trabajo a partir del remitente."""
    sender = (sender_value or "").lower()

    if "agencia" in sender:
        return "Agencia"
    if "cliente" in sender:
        return "B2B"
    if "proveedor" in sender:
        return "Operativo"

    return ""


def _send_auto_reply_if_applicable(access_token, account_id, account_email, email_data):
    """Envía respuesta automática si el correo cumple la regla de negocio."""
    if not should_send_auto_reply(email_data):
        return False

    sender_email = (email_data.get("sender_email") or "").strip().lower()
    if not sender_email:
        return False

    if account_email and sender_email == account_email.strip().lower():
        return False

    send_mail(
        access_token=access_token,
        account_id=account_id,
        to_address=sender_email,
        subject=f"Re: {email_data.get('subject', 'Sin asunto')}",
        content=AUTO_REPLY_MESSAGE,
        from_address=account_email
    )
    return True


def _build_alert_message(sender, subject):
    """Construye el cuerpo del correo de alerta de pendientes."""
    return (
        "Asunto: Recordatorio de correo pendiente\n\n"
        "Tienes un correo pendiente de respuesta desde hace más de 24 horas.\n\n"
        f"Remitente: {sender}\n"
        f"Asunto: {subject}\n\n"
        "Te recomiendo revisarlo lo antes posible."
    )


def _is_zoho_send_auth_error(error):
    """Detecta errores de autenticación/scope al enviar correo por Zoho."""
    response = getattr(error, "response", None)
    if response is None:
        return False

    if response.status_code in (401, 403):
        return True

    body = (getattr(response, "text", "") or "").lower()
    return "invalid_oauthscope" in body or "authfail" in body


def main():
    # Carga variables desde el archivo .env local.
    _load_dotenv_file()

    required_env = [
        "NOTION_TOKEN",
        "NOTION_DATABASE_ID",
        "ZOHO_CLIENT_ID",
        "ZOHO_CLIENT_SECRET",
        "ZOHO_REFRESH_TOKEN"
    ]

    if not _validate_env(required_env):
        return

    notion_token = os.getenv("NOTION_TOKEN")
    notion_database_id = os.getenv("NOTION_DATABASE_ID")
    zoho_client_id = os.getenv("ZOHO_CLIENT_ID")
    zoho_client_secret = os.getenv("ZOHO_CLIENT_SECRET")
    zoho_refresh_token = os.getenv("ZOHO_REFRESH_TOKEN")
    zoho_account_id = os.getenv("ZOHO_ACCOUNT_ID")
    zoho_alert_to = os.getenv("ZOHO_ALERT_TO")

    try:
        # 1) Genera access token dinámico usando refresh token de Zoho.
        access_token = get_access_token(
            client_id=zoho_client_id,
            client_secret=zoho_client_secret,
            refresh_token=zoho_refresh_token
        )

        # 2) Obtiene cuenta Zoho (ID y correo remitente cuando está disponible).
        account_info = get_account_info(access_token, explicit_account_id=zoho_account_id)
        account_id = account_info["account_id"]
        account_email = account_info.get("email_address")

        # 3) Consulta correos desde Zoho.
        emails = get_messages(access_token, account_id)
    except (RequestException, ValueError) as error:
        print(f"Error consultando Zoho Mail: {error}")
        return

    if not emails:
        print("No se encontraron correos para procesar.")
        emails = []

    state_file_path = Path(__file__).with_name(STATE_FILE_NAME)
    runtime_state = _load_runtime_state(state_file_path)

    processed_ids_ordered = runtime_state["processed_ids"]
    alerted_task_ids_ordered = runtime_state["alerted_task_ids"]
    processed_ids = set(processed_ids_ordered)
    alerted_task_ids = set(alerted_task_ids_ordered)

    created_tasks = 0
    skipped_emails = 0
    skipped_duplicates = 0
    auto_replies_sent = 0
    alerts_sent = 0
    zoho_send_enabled = True

    # 4) Itera correos; parsea campos y crea una tarea por correo nuevo.
    for raw_email in emails:
        if not is_new_email(raw_email):
            skipped_emails += 1
            continue

        email_data = parse_email(raw_email)
        email_data["source_label"] = "Correo"
        email_data["area"] = _classify_area(
            email_data.get("sender_email") or email_data.get("sender")
        )
        email_key = _email_unique_key(raw_email, email_data)

        # Evita crear una tarea duplicada para el mismo correo.
        if email_key in processed_ids:
            skipped_duplicates += 1
            continue

        try:
            create_task(notion_token, notion_database_id, email_data)
            created_tasks += 1
            processed_ids.add(email_key)
            processed_ids_ordered.append(email_key)
            print(f"Tarea creada: {email_data['subject']}")

            if zoho_send_enabled:
                try:
                    if _send_auto_reply_if_applicable(
                        access_token=access_token,
                        account_id=account_id,
                        account_email=account_email,
                        email_data=email_data
                    ):
                        auto_replies_sent += 1
                        print(f"Auto-respuesta enviada a: {email_data.get('sender_email')}")
                except RequestException as error:
                    print(f"Error enviando auto-respuesta para '{email_data['subject']}': {error}")
                    if _is_zoho_send_auth_error(error):
                        zoho_send_enabled = False
                        print("Se desactivan envíos en esta ejecución: faltan permisos/scope de Zoho para enviar correo.")
        except RequestException as error:
            print(f"Error creando tarea para '{email_data['subject']}': {error}")

    alert_recipient = (zoho_alert_to or account_email or "").strip()
    if alert_recipient:
        try:
            overdue_tasks = get_overdue_unanswered_tasks(
                notion_token=notion_token,
                notion_database_id=notion_database_id,
                older_than_hours=24
            )
        except RequestException as error:
            overdue_tasks = []
            print(f"Error consultando alertas en Notion: {error}")

        for task in overdue_tasks:
            page_id = task.get("page_id")
            if not page_id or page_id in alerted_task_ids:
                continue

            sender = task.get("sender") or "Desconocido"
            subject = task.get("subject") or "Sin asunto"

            if not zoho_send_enabled:
                break

            try:
                send_mail(
                    access_token=access_token,
                    account_id=account_id,
                    to_address=alert_recipient,
                    subject=ALERT_SUBJECT,
                    content=_build_alert_message(sender, subject),
                    from_address=account_email
                )
                alerts_sent += 1
                alerted_task_ids.add(page_id)
                alerted_task_ids_ordered.append(page_id)
            except RequestException as error:
                print(f"Error enviando alerta para '{subject}': {error}")
                if _is_zoho_send_auth_error(error):
                    zoho_send_enabled = False
                    print("Se desactivan alertas por falta de permisos/scope de envío en Zoho.")
                    break
    else:
        print("No se encontró correo destino para alertas (usa ZOHO_ALERT_TO opcional).")

    _save_runtime_state(
        state_file_path,
        processed_ids_ordered=processed_ids_ordered,
        alerted_task_ids_ordered=alerted_task_ids_ordered
    )

    print("Proceso finalizado")
    print(f"- Correos recibidos: {len(emails)}")
    print(f"- Correos omitidos (no nuevos): {skipped_emails}")
    print(f"- Correos omitidos (duplicados): {skipped_duplicates}")
    print(f"- Tareas creadas: {created_tasks}")
    print(f"- Auto-respuestas enviadas: {auto_replies_sent}")
    print(f"- Alertas enviadas (24h): {alerts_sent}")


if __name__ == "__main__":
    main()
