from datetime import datetime, timezone
import re


def _first_non_empty(email, keys):
    """Devuelve el primer valor no vacío de una lista de claves posibles."""
    for key in keys:
        value = email.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_sender(sender_value):
    """Normaliza el remitente para devolver texto plano."""
    if isinstance(sender_value, str):
        return sender_value.strip()

    if isinstance(sender_value, dict):
        for key in ("address", "email", "mailAddress", "name"):
            value = sender_value.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return "Desconocido"


def _extract_email_from_text(value):
    """Extrae una dirección de correo desde texto libre."""
    if not isinstance(value, str):
        return ""

    match = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", value)
    return match.group(0).lower() if match else ""


def _collect_emails(raw_value):
    """Normaliza distintas estructuras de Zoho para extraer emails."""
    emails = []

    if isinstance(raw_value, str):
        pieces = re.split(r"[,;]", raw_value)
        for piece in pieces:
            found = _extract_email_from_text(piece.strip())
            if found:
                emails.append(found)
        return emails

    if isinstance(raw_value, dict):
        for key in ("address", "email", "mailAddress", "value"):
            value = raw_value.get(key)
            found = _extract_email_from_text(value) if isinstance(value, str) else ""
            if found:
                emails.append(found)
        return emails

    if isinstance(raw_value, list):
        for item in raw_value:
            emails.extend(_collect_emails(item))
        return emails

    return emails


def _to_iso_date(raw_date):
    """Convierte fechas de Zoho a formato ISO compatible con Notion."""
    if raw_date is None:
        return datetime.now(timezone.utc).isoformat()

    if isinstance(raw_date, (int, float)):
        # Zoho puede devolver timestamp en milisegundos.
        if raw_date > 10**12:
            raw_date = raw_date / 1000
        return datetime.fromtimestamp(raw_date, tz=timezone.utc).isoformat()

    if isinstance(raw_date, str):
        value = raw_date.strip()
        if value.isdigit():
            numeric = int(value)
            if numeric > 10**12:
                numeric = numeric / 1000
            return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat()

        # Si ya es una fecha ISO o textual, se envía tal cual.
        return value

    return datetime.now(timezone.utc).isoformat()


def _extract_sender_email(email):
    """Obtiene correo del remitente con varios campos posibles."""
    for key in ("fromAddress", "from", "sender", "mailFrom"):
        candidate = email.get(key)
        found = _collect_emails(candidate)
        if found:
            return found[0]

    return ""


def _extract_recipients(email):
    """Obtiene lista de destinatarios del mensaje recibido."""
    recipients = []
    for key in ("toAddress", "to", "toEmail", "toEmails", "recipients"):
        recipients.extend(_collect_emails(email.get(key)))

    deduplicated = []
    seen = set()
    for recipient in recipients:
        if recipient not in seen:
            deduplicated.append(recipient)
            seen.add(recipient)

    return deduplicated


def _looks_automatic_or_list(email, sender_email):
    """Marca correos automáticos/listas usando señales comunes."""
    sender = (sender_email or "").lower()
    sender_patterns = (
        "noreply",
        "no-reply",
        "donotreply",
        "do-not-reply",
        "mailer-daemon",
        "postmaster",
        "newsletter",
        "bounce"
    )
    if any(pattern in sender for pattern in sender_patterns):
        return True

    for key in ("isAutoReply", "autoReply", "isNotification", "isBulkMail"):
        value = email.get(key)
        if value is True:
            return True

    for key in ("headers", "messageHeaders"):
        raw_headers = email.get(key)
        if not isinstance(raw_headers, dict):
            continue

        flattened = " ".join(str(value).lower() for value in raw_headers.values())
        if any(token in flattened for token in ("auto-submitted", "list-unsubscribe", "precedence: bulk", "precedence: list")):
            return True

    return False


def is_new_email(email):
    """Intenta identificar correos nuevos/no leídos según banderas comunes."""
    if not isinstance(email, dict):
        return False

    unread_value = email.get("unread")
    if isinstance(unread_value, bool):
        return unread_value

    read_value = email.get("read")
    if isinstance(read_value, bool):
        return not read_value

    seen_value = email.get("seen")
    if isinstance(seen_value, bool):
        return not seen_value

    # Si Zoho no envía bandera, se procesa el correo para no perderlo.
    return True


def parse_email(email):
    """Extrae campos clave: subject, sender, body y date."""
    if not isinstance(email, dict):
        return {
            "subject": "Sin asunto",
            "sender": "Desconocido",
            "body": "",
            "date": datetime.now(timezone.utc).isoformat()
        }

    subject = _first_non_empty(email, ("subject", "mailSubject", "summary")) or "Sin asunto"

    sender_raw = email.get("fromAddress") or email.get("from") or email.get("sender")
    sender = _normalize_sender(sender_raw)
    sender_email = _extract_sender_email(email) or _extract_email_from_text(sender)

    body = _first_non_empty(email, ("content", "body", "snippet", "summary", "plainText"))

    raw_date = email.get("receivedTime") or email.get("receivedTimeInGMT") or email.get("date")
    date = _to_iso_date(raw_date)

    recipients = _extract_recipients(email)
    is_automatic_or_list = _looks_automatic_or_list(email, sender_email)

    return {
        "subject": subject,
        "sender": sender,
        "sender_email": sender_email,
        "recipients": recipients,
        "is_automatic_or_list": is_automatic_or_list,
        "body": body,
        "date": date
    }


def should_send_auto_reply(email_data):
    """Regla de negocio para auto-responder correos individuales no automáticos."""
    recipients = email_data.get("recipients", [])
    sender_email = (email_data.get("sender_email") or "").strip()
    is_automatic_or_list = bool(email_data.get("is_automatic_or_list"))

    return len(recipients) == 1 and bool(sender_email) and not is_automatic_or_list
