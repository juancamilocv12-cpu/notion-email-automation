import argparse
import re
from pathlib import Path

import requests


def _extract_code(raw_value):
    """Acepta code directo o URL de callback y retorna el authorization code."""
    value = (raw_value or "").strip()
    if not value:
        return ""

    match = re.search(r"[?&]code=([^&]+)", value)
    if match:
        return match.group(1)

    return value


def _load_env_file(env_path):
    """Lee .env en formato simple KEY=VALUE."""
    data = {}
    if not env_path.exists():
        return data

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        data[key.strip()] = value.strip()

    return data


def _upsert_env_value(env_path, key, value):
    """Actualiza o inserta una variable en .env preservando el resto del archivo."""
    lines = []
    found = False

    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    updated_lines = []
    for line in lines:
        if line.startswith(f"{key}="):
            updated_lines.append(f"{key}={value}")
            found = True
        else:
            updated_lines.append(line)

    if not found:
        if updated_lines and updated_lines[-1].strip() != "":
            updated_lines.append("")
        updated_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")


def _exchange_code_for_tokens(client_id, client_secret, code, redirect_uri):
    """Canjea authorization code por access/refresh token en Zoho OAuth."""
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }

    response = requests.post("https://accounts.zoho.com/oauth/v2/token", data=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser(
        description="Renueva ZOHO_REFRESH_TOKEN usando un authorization code y actualiza .env"
    )
    parser.add_argument("--code", help="Authorization code o callback URL con code=")
    parser.add_argument("--redirect-uri", default="http://localhost", help="Redirect URI usado al crear el code")
    parser.add_argument("--env-file", default=".env", help="Ruta del archivo .env")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    env_data = _load_env_file(env_path)

    client_id = env_data.get("ZOHO_CLIENT_ID", "").strip()
    client_secret = env_data.get("ZOHO_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        print("Faltan ZOHO_CLIENT_ID o ZOHO_CLIENT_SECRET en .env")
        return

    raw_code = args.code or input("Pega aquí el authorization code o callback URL: ").strip()
    code = _extract_code(raw_code)
    if not code:
        print("No se pudo extraer un authorization code válido.")
        return

    try:
        token_data = _exchange_code_for_tokens(
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=args.redirect_uri,
        )
    except requests.RequestException as error:
        print(f"Error canjeando code en Zoho: {error}")
        return

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        print("Zoho no devolvió refresh_token. Revisa que el code no esté usado/expirado.")
        print(token_data)
        return

    _upsert_env_value(env_path, "ZOHO_REFRESH_TOKEN", refresh_token)

    scope = token_data.get("scope", "")
    print("Refresh token actualizado en .env")
    print(f"Scope recibido: {scope}")


if __name__ == "__main__":
    main()
