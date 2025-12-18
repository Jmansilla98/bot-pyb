import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import os
import traceback

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SHEET_NAME = "Matches"
CREDENTIALS_FILE = "credentials.json"


# ------------------------
# VALIDACIÓN DE ESTADO
# ------------------------
def validate_state(state: dict) -> bool:
    required_keys = ["channel_id", "channel_name", "mode", "teams", "maps"]
    for key in required_keys:
        if key not in state:
            print(f"[SHEETS] Falta clave: {key}")
            return False

    if len(state.get("teams", [])) != 2:
        print("[SHEETS] Teams inválidos")
        return False

    return True


# ------------------------
# SERIALIZACIÓN
# ------------------------
def serialize_maps(maps: list) -> str:
    out = []
    for m in maps:
        if not m or "map" not in m:
            continue

        out.append(
            f'{m.get("map","?")} '
            f'(A:{m.get("side_a","?")}/B:{m.get("side_b","?")}) '
        )
    return "\n".join(out)


def serialize_dict_block(data: dict) -> str:
    if not data:
        return ""
    return "\n".join(
        f"{team}: {', '.join(items)}"
        for team, items in data.items() if items
    )


# ------------------------
# ENVÍO A GOOGLE SHEETS
# ------------------------
def send_match_to_sheets(state: dict):
    if not validate_state(state):
        return

    if not SHEET_ID:
        print("[SHEETS] GOOGLE_SHEET_ID no definido")
        return

    try:
        creds = Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=SCOPES
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)

        row = [
            datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            state.get("channel_name"),
            str(state.get("channel_id")),
            state.get("mode"),
            state["teams"][0],
            state["teams"][1],
            serialize_maps(state.get("maps", [])),
            serialize_dict_block(state.get("bans", {})),
            serialize_dict_block(state.get("picks", {})),
        ]

        sheet.append_row(row, value_input_option="USER_ENTERED")
        print(f"[SHEETS] Exportado canal {state['channel_id']}")

    except Exception as e:
        print("[SHEETS][ERROR]")
        print(traceback.format_exc())
