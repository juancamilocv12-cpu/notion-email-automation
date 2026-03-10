import requests


def _extract_list(payload, keys):
    """Busca listas en distintos nodos posibles del JSON de Zoho."""
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value

    data_node = payload.get("data")
    if isinstance(data_node, dict):
        for key in keys:
            value = data_node.get(key)
            if isinstance(value, list):
                return value

    return []


def _extract_account_email(account):
    """Obtiene una dirección de correo utilizable desde datos de cuenta Zoho."""
    if not isinstance(account, dict):
        return None

    for key in (
        "mailAddress",
        "emailAddress",
        "primaryEmailAddress",
        "accountName",
        "displayMailAddress"
    ):
        value = account.get(key)
        if isinstance(value, str) and "@" in value:
            return value.strip()

    return None


def get_access_token(client_id, client_secret, refresh_token):
    """Obtiene un access token de Zoho a partir del refresh token."""
    url = "https://accounts.zoho.com/oauth/v2/token"
    payload = {
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token"
    }

    response = requests.post(url, data=payload, timeout=30)
    response.raise_for_status()

    token_data = response.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise ValueError("No se pudo obtener access_token desde Zoho.")

    return access_token


def get_account_id(access_token, explicit_account_id=None):
    """Retorna accountId; usa variable opcional o detecta la primera cuenta disponible."""
    if explicit_account_id:
        return explicit_account_id

    url = "https://mail.zoho.com/api/accounts"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}"
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    payload = response.json()
    accounts = _extract_list(payload, ("accounts", "data"))

    for account in accounts:
        if not isinstance(account, dict):
            continue
        account_id = account.get("accountId") or account.get("id")
        if account_id:
            return str(account_id)

    raise ValueError("No se encontró accountId en la respuesta de Zoho.")


def get_account_info(access_token, explicit_account_id=None):
    """Retorna accountId y email de la cuenta Zoho."""
    if explicit_account_id:
        return {
            "account_id": str(explicit_account_id),
            "email_address": None
        }

    url = "https://mail.zoho.com/api/accounts"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}"
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    payload = response.json()
    accounts = _extract_list(payload, ("accounts", "data"))

    for account in accounts:
        if not isinstance(account, dict):
            continue

        account_id = account.get("accountId") or account.get("id")
        if not account_id:
            continue

        return {
            "account_id": str(account_id),
            "email_address": _extract_account_email(account)
        }

    raise ValueError("No se encontró accountId en la respuesta de Zoho.")


def get_messages(access_token, account_id):
    """Consulta mensajes desde Zoho Mail usando el endpoint de view."""
    url = f"https://mail.zoho.com/api/accounts/{account_id}/messages/view"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}"
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    payload = response.json()
    return _extract_list(payload, ("messages", "data", "emails", "mailList"))


def send_mail(access_token, account_id, to_address, subject, content, from_address=None):
    """Envía un correo desde Zoho Mail API."""
    if not to_address:
        raise ValueError("to_address es obligatorio para enviar correo.")

    url = f"https://mail.zoho.com/api/accounts/{account_id}/messages"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }

    payload = {
        "toAddress": to_address,
        "subject": subject,
        "content": content,
        "mailFormat": "plaintext"
    }

    if from_address:
        payload["fromAddress"] = from_address

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()
