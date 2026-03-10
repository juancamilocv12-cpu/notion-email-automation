import requests
from datetime import datetime, timedelta, timezone


_DATABASE_PROPERTIES_CACHE = {}


def _headers(notion_token):
    """Encabezados estándar para API de Notion."""
    return {
        "Authorization": f"Bearer {notion_token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }


def _truncate(text, max_len=1900):
    """Notion limita longitud del texto en bloques rich_text."""
    if not isinstance(text, str):
        return ""
    return text[:max_len]


def _parse_datetime(value):
    """Convierte fecha ISO de Notion/Zoho a datetime timezone-aware."""
    if not isinstance(value, str) or not value.strip():
        return None

    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed


def _get_database_properties(notion_token, notion_database_id):
    """Obtiene y cachea propiedades de la base para evitar consultas repetidas."""
    cache_key = (notion_token, notion_database_id)
    if cache_key in _DATABASE_PROPERTIES_CACHE:
        return _DATABASE_PROPERTIES_CACHE[cache_key]

    url = f"https://api.notion.com/v1/databases/{notion_database_id}"
    response = requests.get(url, headers=_headers(notion_token), timeout=30)
    response.raise_for_status()

    properties = response.json().get("properties", {})
    _DATABASE_PROPERTIES_CACHE[cache_key] = properties
    return properties


def _find_property_name(properties, property_type, preferred_names=()):
    """Encuentra nombre de propiedad por tipo, priorizando nombres esperados."""
    for name in preferred_names:
        prop = properties.get(name)
        if isinstance(prop, dict) and prop.get("type") == property_type:
            return name

    for name, prop in properties.items():
        if isinstance(prop, dict) and prop.get("type") == property_type:
            return name

    return None


def _find_property(properties, preferred_names=(), allowed_types=(), allow_any_fallback=True):
    """Encuentra propiedad priorizando nombres y tipos permitidos."""
    for name in preferred_names:
        prop = properties.get(name)
        if not isinstance(prop, dict):
            continue
        if allowed_types and prop.get("type") not in allowed_types:
            continue
        return name, prop

    if allow_any_fallback:
        for name, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            if allowed_types and prop.get("type") not in allowed_types:
                continue
            return name, prop

    return None, None


def _rich_text_value(content):
    """Construye estructura rich_text para propiedades Notion."""
    return {
        "rich_text": [
            {
                "text": {
                    "content": _truncate(content)
                }
            }
        ]
    }


def _extract_plain_text(property_value):
    """Extrae texto plano desde una propiedad de Notion."""
    if not isinstance(property_value, dict):
        return ""

    prop_type = property_value.get("type")
    if prop_type in ("title", "rich_text"):
        chunks = property_value.get(prop_type, [])
        return "".join(chunk.get("plain_text", "") for chunk in chunks if isinstance(chunk, dict)).strip()

    if prop_type in ("select", "status"):
        selected = property_value.get(prop_type)
        if isinstance(selected, dict):
            return (selected.get("name") or "").strip()

    if prop_type == "email":
        return (property_value.get("email") or "").strip()

    if prop_type == "checkbox":
        return "Sí" if property_value.get("checkbox") else "No"

    return ""


def _option_exists(property_definition, option_name, option_type):
    """Valida si existe una opción en select/status de la base."""
    if not isinstance(property_definition, dict):
        return False

    option_block = property_definition.get(option_type)
    if not isinstance(option_block, dict):
        return False

    options = option_block.get("options", [])
    option_names = {
        (item.get("name") or "").strip().lower()
        for item in options
        if isinstance(item, dict)
    }
    return option_name.strip().lower() in option_names


def _find_label_property(properties):
    """Busca una propiedad apta para etiquetar el origen del correo."""
    return _find_property(
        properties,
        preferred_names=("Etiqueta", "Etiquetas", "Tag", "Tags", "Origen", "Fuente", "Canal"),
        allowed_types=("select", "multi_select", "status", "rich_text"),
        allow_any_fallback=False
    )


def _ensure_source_label_property(notion_token, notion_database_id, properties):
    """Crea la propiedad Origen (select) si no existe una propiedad de etiqueta."""
    label_name, _ = _find_label_property(properties)
    if label_name:
        return properties

    try:
        response = requests.patch(
            f"https://api.notion.com/v1/databases/{notion_database_id}",
            headers=_headers(notion_token),
            json={
                "properties": {
                    "Origen": {
                        "select": {
                            "options": [
                                {"name": "Correo", "color": "blue"}
                            ]
                        }
                    }
                }
            },
            timeout=30
        )
        response.raise_for_status()
    except requests.RequestException:
        return properties

    _DATABASE_PROPERTIES_CACHE.pop((notion_token, notion_database_id), None)
    return _get_database_properties(notion_token, notion_database_id)


def _set_responded_default(notion_properties, responded_name, responded_def):
    """Intenta inicializar Respondido = No según tipo de propiedad."""
    if not responded_name or not isinstance(responded_def, dict):
        return

    prop_type = responded_def.get("type")
    if prop_type == "checkbox":
        notion_properties[responded_name] = {"checkbox": False}
        return

    if prop_type == "select":
        selected_name = "No" if _option_exists(responded_def, "No", "select") else None
        if selected_name:
            notion_properties[responded_name] = {"select": {"name": selected_name}}
        return

    if prop_type == "status":
        if _option_exists(responded_def, "No", "status"):
            notion_properties[responded_name] = {"status": {"name": "No"}}
        elif _option_exists(responded_def, "Backlog", "status"):
            notion_properties[responded_name] = {"status": {"name": "Backlog"}}
        return

    if prop_type == "rich_text":
        notion_properties[responded_name] = _rich_text_value("No")


def _build_payload(notion_database_id, email_data, properties):
    """Arma payload usando el esquema real de la base de datos de Notion."""
    title_property = _find_property_name(
        properties,
        "title",
        preferred_names=("Tarea", "TAREAS HOY ", "Name", "Nombre")
    )
    if not title_property:
        raise ValueError("La base de Notion no tiene una propiedad tipo title.")

    description_property, _ = _find_property(
        properties,
        preferred_names=("Descripción", "Descripcion", "Description", "Detalle"),
        allowed_types=("rich_text",)
    )
    sender_property, sender_definition = _find_property(
        properties,
        preferred_names=("Remitente", "Sender", "From", "Correo"),
        allowed_types=("rich_text", "email")
    )
    if sender_property == description_property:
        sender_property, sender_definition = None, None

    date_property = _find_property_name(
        properties,
        "date",
        preferred_names=("Fecha", "Fecha Limite", "Date")
    )

    status_property = _find_property_name(
        properties,
        "status",
        preferred_names=("Estado", "Status")
    )
    select_status_property = None
    if not status_property:
        select_status_property = _find_property_name(
            properties,
            "select",
            preferred_names=("Estado", "Status")
        )

    area_property, area_definition = _find_property(
        properties,
        preferred_names=("Area", "Área"),
        allowed_types=("select", "status"),
        allow_any_fallback=False
    )
    responded_property, responded_definition = _find_property(
        properties,
        preferred_names=("Respondido", "Respuesta", "Replied"),
        allowed_types=("checkbox", "select", "status", "rich_text"),
        allow_any_fallback=False
    )
    label_property, label_definition = _find_label_property(properties)

    notion_properties = {
        title_property: {
            "title": [
                {
                    "text": {
                        "content": _truncate(email_data["subject"], 200)
                    }
                }
            ]
        }
    }

    if description_property:
        notion_properties[description_property] = _rich_text_value(email_data["body"])

    if sender_property:
        sender_type = sender_definition.get("type")
        if sender_type == "email" and email_data.get("sender_email"):
            notion_properties[sender_property] = {"email": email_data["sender_email"]}
        else:
            notion_properties[sender_property] = _rich_text_value(email_data["sender"])

    if date_property:
        notion_properties[date_property] = {
            "date": {
                "start": email_data["date"]
            }
        }

    if status_property:
        notion_properties[status_property] = {
            "status": {
                "name": "Backlog"
            }
        }
    elif select_status_property:
        notion_properties[select_status_property] = {
            "select": {
                "name": "Backlog"
            }
        }

    area_value = (email_data.get("area") or "").strip()
    if area_property and area_value:
        area_type = area_definition.get("type")
        if area_type == "status":
            notion_properties[area_property] = {"status": {"name": area_value}}
        else:
            notion_properties[area_property] = {"select": {"name": area_value}}

    _set_responded_default(notion_properties, responded_property, responded_definition)

    source_label = (email_data.get("source_label") or "").strip()
    label_applied = False
    if label_property and source_label:
        label_type = label_definition.get("type")
        if label_type == "select":
            notion_properties[label_property] = {"select": {"name": source_label}}
            label_applied = True
        elif label_type == "multi_select":
            notion_properties[label_property] = {"multi_select": [{"name": source_label}]}
            label_applied = True
        elif label_type == "status" and _option_exists(label_definition, source_label, "status"):
            notion_properties[label_property] = {"status": {"name": source_label}}
            label_applied = True
        elif label_type == "rich_text":
            notion_properties[label_property] = _rich_text_value(source_label)
            label_applied = True

    children = []

    # Si faltan propiedades en base, se guarda la información en el cuerpo de la página.
    if not sender_property:
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": f"Remitente: {email_data['sender']}"
                        }
                    }
                ]
            }
        })

    if not date_property:
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": f"Fecha: {email_data['date']}"
                        }
                    }
                ]
            }
        })

    if not description_property:
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": f"Descripción: {_truncate(email_data['body'])}"
                        }
                    }
                ]
            }
        })

    if source_label and not label_applied:
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": f"Etiqueta: {source_label}"
                        }
                    }
                ]
            }
        })

    payload = {
        "parent": {
            "database_id": notion_database_id
        },
        "properties": notion_properties
    }

    if children:
        payload["children"] = children

    return payload


def create_task(notion_token, notion_database_id, email_data):
    """Crea una página/tarea en la base de datos de Notion usando API oficial."""
    properties = _get_database_properties(notion_token, notion_database_id)
    properties = _ensure_source_label_property(notion_token, notion_database_id, properties)
    payload = _build_payload(notion_database_id, email_data, properties)

    response = requests.post(
        "https://api.notion.com/v1/pages",
        headers=_headers(notion_token),
        json=payload,
        timeout=30
    )

    response.raise_for_status()
    return response.json()


def _is_unanswered(properties_payload, responded_property_name, status_property_name):
    """Evalúa si una tarea sigue sin responder."""
    if responded_property_name:
        responded_value = properties_payload.get(responded_property_name)
        if not isinstance(responded_value, dict):
            return False

        responded_type = responded_value.get("type")
        if responded_type == "checkbox":
            return not bool(responded_value.get("checkbox"))

        text = _extract_plain_text(responded_value).strip().lower()
        return text in {"no", "pendiente", "sin responder", "backlog", "false"}

    if status_property_name:
        status_value = properties_payload.get(status_property_name)
        status_text = _extract_plain_text(status_value).strip().lower()
        return status_text not in {"completado", "complete", "done", "respondido", "cerrado"}

    return False


def _extract_sender_from_blocks(notion_token, page_id):
    """Lee bloques de página y extrae remitente si está en contenido."""
    url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=50"
    response = requests.get(url, headers=_headers(notion_token), timeout=30)
    response.raise_for_status()

    results = response.json().get("results", [])
    for block in results:
        if not isinstance(block, dict):
            continue

        paragraph = block.get("paragraph")
        if not isinstance(paragraph, dict):
            continue

        rich_text = paragraph.get("rich_text", [])
        text = "".join(
            item.get("plain_text", "")
            for item in rich_text
            if isinstance(item, dict)
        ).strip()
        if text.lower().startswith("remitente:"):
            return text.split(":", 1)[1].strip()

    return "Desconocido"


def get_overdue_unanswered_tasks(notion_token, notion_database_id, older_than_hours=24):
    """Obtiene tareas no respondidas con más de N horas para alertar."""
    database_properties = _get_database_properties(notion_token, notion_database_id)

    title_property_name = _find_property_name(
        database_properties,
        "title",
        preferred_names=("Tarea", "TAREAS HOY ", "Name", "Nombre")
    )
    date_property_name = _find_property_name(
        database_properties,
        "date",
        preferred_names=("Fecha", "Fecha Limite", "Date")
    )

    responded_property_name, _ = _find_property(
        database_properties,
        preferred_names=("Respondido", "Respuesta", "Replied"),
        allowed_types=("checkbox", "select", "status", "rich_text"),
        allow_any_fallback=False
    )
    status_property_name = _find_property_name(
        database_properties,
        "status",
        preferred_names=("Estado", "Status")
    )
    sender_property_name, _ = _find_property(
        database_properties,
        preferred_names=("Remitente", "Sender", "From", "Correo"),
        allowed_types=("rich_text", "email", "select"),
        allow_any_fallback=False
    )

    if not title_property_name or not date_property_name:
        return []

    threshold = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    cursor = None
    overdue = []

    while True:
        query_payload = {"page_size": 100}
        if cursor:
            query_payload["start_cursor"] = cursor

        response = requests.post(
            f"https://api.notion.com/v1/databases/{notion_database_id}/query",
            headers=_headers(notion_token),
            json=query_payload,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()

        for item in data.get("results", []):
            if not isinstance(item, dict):
                continue

            page_id = item.get("id")
            properties_payload = item.get("properties", {})
            if not page_id or not isinstance(properties_payload, dict):
                continue

            if not _is_unanswered(properties_payload, responded_property_name, status_property_name):
                continue

            date_prop = properties_payload.get(date_property_name, {})
            date_start = None
            if isinstance(date_prop, dict):
                date_info = date_prop.get("date")
                if isinstance(date_info, dict):
                    date_start = date_info.get("start")

            task_date = _parse_datetime(date_start)
            if not task_date or task_date > threshold:
                continue

            subject = _extract_plain_text(properties_payload.get(title_property_name)) or "Sin asunto"
            sender = _extract_plain_text(properties_payload.get(sender_property_name)) if sender_property_name else ""
            if not sender:
                try:
                    sender = _extract_sender_from_blocks(notion_token, page_id)
                except requests.RequestException:
                    sender = "Desconocido"

            overdue.append(
                {
                    "page_id": page_id,
                    "subject": subject,
                    "sender": sender,
                    "date": task_date.isoformat()
                }
            )

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return overdue
