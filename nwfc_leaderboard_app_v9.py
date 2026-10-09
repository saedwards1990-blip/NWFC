import streamlit as st
import pandas as pd
import sqlite3
import os
import urllib.request
import urllib.parse
import csv
import io
import json
import base64
import requests
from datetime import datetime

# Helper to convert standard Google Sheet URL to direct, real-time export CSV URL (bypassing 5-minute cache delay)
def convert_to_export_url(url):
    if not url:
        return ""
    url = url.strip()
    # Remove any surrounding quotes
    if url.startswith('"') and url.endswith('"'):
        url = url[1:-1]
    if url.startswith("'") and url.endswith("'"):
        url = url[1:-1]
    if "export?format=csv" in url or "/pub?" in url:
        return url
    if "docs.google.com/spreadsheets/d/" in url:
        parts = url.split("docs.google.com/spreadsheets/d/")    
        if len(parts) > 1:
            subparts = parts[1].split("/")
            spreadsheet_id = subparts[0]
            gid = "0"
            if "gid=" in url:
                gid_part = url.split("gid=")
                if len(gid_part) > 1:
                    gid = gid_part[1].split("&")[0].split("#")[0].split("?")[0]
            return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={gid}"
    return url

st.set_page_config(
    page_title="North Wales Firefighter Challenge (NWFC)",
    page_icon="assets/nwfrs_logo.png",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==========================================
# MOBILE / ACCESSIBILITY CSS
# ==========================================
# Confirmed via a real phone recording (1080x2400): without this, the tab
# bar needs horizontal scrolling sooner than it should. (The old hero-title
# wrapping fix that used to live here was for an st.title() "h1" element;
# the title is now part of the banner below with its own clamp()'d sizing,
# so that rule is gone rather than left dangling against nothing.)
st.markdown("""
<style>
/* Tighter tab labels so more of the 4-tab bar fits before it needs a
   horizontal swipe. */
.stTabs [data-baseweb="tab"] {
    font-size: 0.85rem;
    padding: 8px 10px;
}

/* Slightly smaller dataframe text buys extra columns of width before the
   table's own horizontal scrollbar kicks in on a narrow screen. */
[data-testid="stDataFrame"] * {
    font-size: 13px !important;
}
</style>
""", unsafe_allow_html=True)

# ==========================================
# CLOUD BACKEND CONFIGURATION (GOOGLE SHEETS)
# ==========================================
# These are read from Streamlit secrets by default and are NOT shown to public
# visitors anywhere on the site. An authenticated marshal can override them for
# the current session from the password-protected Marshal Timer & Admin tab —
# see the "Cloud Backend Configuration" expander inside that tab further down.
if 'gsheet_individuals_csv' not in st.session_state:
    st.session_state['gsheet_individuals_csv'] = st.secrets.get("GSHEET_INDIVIDUALS_CSV", "")
if 'gsheet_relays_csv' not in st.session_state:
    st.session_state['gsheet_relays_csv'] = st.secrets.get("GSHEET_RELAYS_CSV", "")
if 'apps_script_url' not in st.session_state:
    st.session_state['apps_script_url'] = st.secrets.get("APPS_SCRIPT_URL", "")

GSHEET_INDIVIDUALS_CSV = st.session_state['gsheet_individuals_csv']
GSHEET_RELAYS_CSV = st.session_state['gsheet_relays_csv']
APPS_SCRIPT_URL = st.session_state['apps_script_url']

DB_PATH = "nwfc_tournament_v8.db"

# Master NWFRS stations list (all 44 stations + HQ and St Asaph)
STATIONS_LIST = [
    "Aberdyfi", "Abergele", "Abersoch", "Amlwch", "Bala", "Bangor", 
    "Barmouth", "Beaumaris", "Benllech", "Betws-y-Coed", "Blaenau Ffestiniog", 
    "Buckley", "Caernarfon", "Cerrigydrudion", "Chirk", 
    "Colwyn Bay", "Conwy", "Corwen", "Deeside", "Denbigh", 
    "Dolgellau", "Flint", "Harlech", "Holyhead", "Johnstown", "Llanberis", 
    "Llandudno", "Llanfairfechan", "Llangefni", "Llangollen", "Llanrwst", 
    "Menai Bridge", "Mold", "Nefyn", "Porthmadog", "Prestatyn", 
    "Pwllheli", "Rhyl", "Ruthin", "Rhosneigr", "St Asaph", "Tywyn", "Wrexham", "HQ", "Other"
]

# Master watch and departments list (including newly requested sectors and watches)
WATCHES_LIST = [
    "Red", "Green", "Blue", "White", "Nucleus", "RDS", "Rural",
    "Transformation", "Officers", "Training", "Prevention", "HR", "Fleet",
    "Corporate Comms", "Technical Ops", "Facilities", "ICT", "Finance",
    "Response", "Control", "Other"
]

# ==========================================================================
# SINGLE SOURCE OF TRUTH — course stations and penalties.
#
# Every place that used to repeat this content by hand — the Marshal entry-
# form checkboxes, the public Rules & Penalties table, the Station-by-
# Station Guide's per-station penalty text, and the printable PDF forms
# further down — now reads from these two lists instead. That exists
# because the old hand-maintained copies drifted: a wording change was
# made to the entry forms and the Rules table but missed the Station Guide.
# Change a penalty or a station here once; every surface below updates.
# ==========================================================================
COURSE_STATIONS = [
    {
        "key": "shuttle",
        "title": "1. 35m Shuttle",
        "desc": "Run the full 35m of the course to the Cleveland Roll",
        "acceptable": "Can be completed at any pace. Its just a shuttle run ... ",
    },
    {
        "key": "cleveland_carry",
        "title": "2. 30m Cleveland Roll Carry",
        "desc": "Pick up the CLeveland Roll and carry however you see fit. Run back toward the start line, place onto the crach mat",
        "acceptable": "A dropped cleveland roll will incur a penalty. Incorrect placement on the crash mat will also incur a penalty",
    },
    {
        "key": "rtc_carry",
        "title": "3. RTC Tool Carry",
        "desc": "Pick up one RTC Tool with either 1 or 2 hands. Transport one at a time to the RTC Tool Table.",
        "acceptable": "RTC Tool dropped or slammed down will incur a penalty. Incorrect placement outside of the designated area on the table will be a penalty",
    },
    {
        "key": "force_machine",
        "title": "4. Corhaven Force Entry Machine",
        "desc": "Feet either side of the plate, pick up the hammer and strike squarely on the plate until it moves enough to see a green marker or the marshal tells you to stop. Place the hammer on the placement mat",
        "acceptable": "Incorrect technique - Missing the plate / Using the side of the hammer head will incur penalties. 1st offence will be a warning. 2nd will be a penalty. 3rd Warning, you will be told to stop. Incorrect placement of hammer on mat will also incur a penalty",
    },
    {
        "key": "hose_drag",
        "title": "5. 35m Hose Drag",
        "desc": "Pick up and run with the Hose. Carry under arm or over the shoulder. Place the branch over the line and on the mat.",
        "acceptable": "Not placing the branch on the mat and failing to place it past the line will both incur penalties.",
    },
    {
        "key": "hose_makeup",
        "title": "6. 25m Hose Make Up (Rolled Hose Carry for Non-Ops)",
        "desc": "Make up the hose and place into the hose box",
        "acceptable": "Hose must fit in the box, any hose over the top of the box will incur a penalty.",
    },
    {
        "key": "containers",
        "title": "7. 4x 20kg Container Carry",
        "desc": "Carry 2 containers at a time and place in the empty tray. Repeat with the remaining 2.",
        "acceptable": "Containers must not be thrown, slid, or seated incorrectly within the tray, this will incur a penalty",
    },
    {
        "key": "dummy_drag",
        "title": "8. 50m Dummy Drag / Casualty Rescue (70kg Operational / 50kg Non-Ops)",
        "desc": "Pick up dunmmy and drag backward 25m to the hose box, turn and return the 25m to the finish line.",
        "acceptable": "Dummy must not be lifted completely off the ground, or dragged by the face or neck. Dummy's feet must cross the finish line for the timer to stop.",
    },
]

PENALTIES = [
    {"code": "P1", "desc": "Dropped Cleveland Hose Pack / Incorrect Placement", "pts": 5, "label": "+5s",
     "station_key": "cleveland_carry", "relay_only": False},
    {"code": "P2", "desc": "Improper RTC Tool Table Placement (1st Tool)", "pts": 5, "label": "+5s",
     "station_key": "rtc_carry", "relay_only": False},
    {"code": "P2B", "desc": "Improper RTC Tool Table Placement (2nd Tool)", "pts": 5, "label": "+5s",
     "station_key": "rtc_carry", "relay_only": False},
    {"code": "P3", "desc": "Improper Forcible Entry Sledge Technique (2nd warning is a penalty)", "pts": 10,
     "label": "+10s", "station_key": "force_machine", "relay_only": False},
    {"code": "P3B", "desc": "Improper Hammer Placement on Hammer Placement Mat", "pts": 5,
     "label": "+5s", "station_key": "force_machine", "relay_only": False},
    {"code": "P4", "desc": "Multiple Improper Forcible Entry Machine Warnings (told to stop and move to next station)",
     "pts": 30, "label": "+30s", "station_key": "force_machine", "relay_only": False},
    {"code": "P5", "desc": "Missed 35m Hose Drag Marker", "pts": 10, "label": "+10s",
     "station_key": "hose_drag", "relay_only": False},
    {"code": "P6", "desc": "Hose Makeup Box Overflow Boundary", "pts": 10, "label": "+10s",
     "station_key": "hose_makeup", "relay_only": False},
    {"code": "P7", "desc": "Foam Containers Slid / Thrown / Not Seated Correctly Within Tray", "pts": 10,
     "label": "+10s", "station_key": "containers", "relay_only": False},
    {"code": "P8", "desc": "Dummy Head / Face Drag or Feet Lifted Off Ground (1st warning, 2nd warning is a penalty)",
     "pts": 15, "label": "+15s", "station_key": "dummy_drag", "relay_only": False},
    {"code": "P9", "desc": "Relay Touch-Tag Missed or Out of Zone", "pts": 10, "label": "+10s",
     "station_key": None, "relay_only": True},
    {"code": "P10", "desc": "Dummy Drag Boundary Lane Crossing", "pts": 15, "label": "+15s",
     "station_key": "dummy_drag", "relay_only": True},
]

def station_penalty_text(station_key):
    """Builds the Station Guide's 'Penalty if breached' line straight from PENALTIES."""
    matches = [p for p in PENALTIES if p["station_key"] == station_key and not p["relay_only"]]
    if not matches:
        return "No dedicated penalty code for this station — see Rules & Penalties for anything that applies here."
    return " ".join(f"{p['desc']} — {p['label']}." for p in matches)

# Local SQLite fallback initialisation
def init_local_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS individuals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            Name TEXT NOT NULL,
            Station TEXT NOT NULL,
            Watch TEXT NOT NULL,
            Category TEXT NOT NULL,
            Age_Group TEXT NOT NULL,
            Raw_Time_sec REAL NOT NULL,
            Penalties_sec REAL DEFAULT 0,
            Final_Time_sec REAL NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS relays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            Station TEXT NOT NULL, -- Will store 'Relay Team Name'
            Watch TEXT NOT NULL, -- Stores 'N/A'
            Division TEXT NOT NULL, -- 'Male', 'Female', 'Mixed'
            Runner_1 TEXT NOT NULL,
            Runner_2 TEXT NOT NULL,
            Runner_3 TEXT NOT NULL,
            Runner_4 TEXT NOT NULL,
            Raw_Time_sec REAL NOT NULL,
            Penalties_sec REAL DEFAULT 0,
            Final_Time_sec REAL NOT NULL
        )
    ''')
    c.execute("SELECT COUNT(*) FROM individuals")
    if c.fetchone()[0] == 0:
        mock_ind = [
            ("Dylan Hughes", "Wrexham", "Red", "Operational Male", "30-34", 112.5, 5, 117.5),
            ("Gareth Jones", "Rhyl", "Green", "Operational Male", "18-29", 108.0, 0, 108.0),
            ("Aled Roberts", "Deeside", "Blue", "Operational Male", "40-44", 125.0, 10, 135.0),
            ("Sion Williams", "Bangor", "Red", "Operational Male", "35-39", 118.5, 0, 118.5),
            ("Iwan Davies", "Holyhead", "Blue", "Operational Male", "45-49", 131.0, 5, 136.0),
            ("Owain Evans", "Colwyn Bay", "Green", "Operational Male", "50-54", 138.5, 0, 138.5),
            ("Dewi Thomas", "Llandudno", "Red", "Operational Male", "55+", 145.0, 10, 155.0),
            ("Ffion Evans", "Wrexham", "Blue", "Operational Female", "18-29", 122.0, 0, 122.0),
            ("Catrin Williams", "Rhyl", "Red", "Operational Female", "30-34", 126.5, 5, 131.5),
            ("Bethan Davies", "Deeside", "Green", "Operational Female", "35-39", 134.0, 0, 134.0),
            ("Elin Roberts", "Bangor", "Blue", "Operational Female", "40-44", 139.0, 10, 149.0),
            ("Sian Jones", "Holyhead", "Green", "Operational Female", "45-49", 148.0, 0, 148.0),
            ("Lowri Thomas", "Colwyn Bay", "Red", "Operational Female", "50-54", 155.0, 5, 160.0),
            ("Heledd Evans", "Llandudno", "Blue", "Operational Female", "55+", 162.0, 0, 162.0),
            ("Nia Jenkins", "Wrexham", "Corporate", "Non-Operational", "30-34", 95.0, 0, 95.0),
            ("Mark Owen", "HQ", "Finance", "Non-Operational", "40-44", 102.5, 5, 107.5),
            ("Sian Parry", "St Asaph", "Control", "Non-Operational", "18-29", 98.0, 0, 98.0),
        ]
        c.executemany("INSERT INTO individuals (name, station, watch, category, age_group, raw_time_sec, penalties_sec, final_time_sec) VALUES (?,?,?,?,?,?,?,?)", mock_ind)
    c.execute("SELECT COUNT(*) FROM relays")
    if c.fetchone()[0] == 0:
        mock_rel = [
            ("Wrexham Red", "N/A", "Male", "Runner A", "Runner B", "Runner C", "Runner D", 195.5, 10, 205.5),
            ("Rhyl Green", "N/A", "Male", "Runner A", "Runner B", "Runner C", "Runner D", 201.0, 0, 201.0),
            ("Deeside Blue", "N/A", "Mixed", "Runner A", "Runner B", "Runner C", "Runner D", 215.5, 5, 220.5),
            ("Bangor Red", "N/A", "Female", "Runner A", "Runner B", "Runner C", "Runner D", 232.0, 0, 232.0),
        ]
        c.executemany("INSERT INTO relays (station, watch, division, runner_1, runner_2, runner_3, runner_4, raw_time_sec, penalties_sec, final_time_sec) VALUES (?,?,?,?,?,?,?,?,?,?)", mock_rel)
    conn.commit()
    conn.close()

# Safe database initialization
try:
    init_local_db()
except Exception as e:
    pass

# Helper to format seconds
def format_time(sec):
    try:
        val = float(sec)
        mins = int(val // 60)
        secs = val % 60
        # High precision format to support milliseconds (3 decimal places)
        return f"{mins:02d}:{secs:06.3f}"
    except:
        return "00:00.000"

# Robust CSV Reader Utility to prevent tokenising errors (e.g. from commas in fields or trailing grid cells)
def get_gsheet_data_robust(url):
    try:
        # Cache buster to ensure standard Google Sheet viewer URLs fetch 100% live data with 0 sync lag
        if "?" in url:
            cache_url = f"{url}&cb={os.urandom(4).hex()}"
        else:
            cache_url = f"{url}?cb={os.urandom(4).hex()}"
            
        req = urllib.request.Request(cache_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as r:
            raw_data = r.read().decode('utf-8')
        
        f = io.StringIO(raw_data)
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return pd.DataFrame()
            
        header = [h.strip() for h in header]
        
        rows = []
        for row in reader:
            # Handle extra or missing columns in lines gracefully without throwing tokenizer errors
            if len(row) > len(header):
                row = row[:len(header)]
            elif len(row) < len(header):
                row += [''] * (len(header) - len(row))
            rows.append(row)
            
        df = pd.DataFrame(rows, columns=header)
        return df
    except Exception as e:
        raise RuntimeError(f"CSV Parsing Failed: {e}")

# Data Access Layer
def get_individuals_data():
    if GSHEET_INDIVIDUALS_CSV:
        try:
            real_url = convert_to_export_url(GSHEET_INDIVIDUALS_CSV)
            df = get_gsheet_data_robust(real_url)
            if 'final_time_sec' in df.columns:
                df['final_time_sec'] = pd.to_numeric(df['final_time_sec'], errors='coerce')
                df = df.dropna(subset=['final_time_sec'])
                return df
            elif df.empty:
                return pd.DataFrame(columns=['id', 'name', 'station', 'watch', 'category', 'age_group', 'raw_time_sec', 'penalties_sec', 'final_time_sec', 'formatted_time'])
        except Exception as e:
            st.error(f"Error reading Individuals from Google Sheets: {e}. Falling back to local data.")
    
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM individuals ORDER BY final_time_sec ASC", conn)
    conn.close()
    return df

def get_relays_data():
    if GSHEET_RELAYS_CSV:
        try:
            real_url = convert_to_export_url(GSHEET_RELAYS_CSV)
            df = get_gsheet_data_robust(real_url)
            if 'final_time_sec' in df.columns:
                df['final_time_sec'] = pd.to_numeric(df['final_time_sec'], errors='coerce')
                df = df.dropna(subset=['final_time_sec'])
                return df
            elif df.empty:
                return pd.DataFrame(columns=['id', 'station', 'watch', 'division', 'runner_1', 'runner_2', 'runner_3', 'runner_4', 'raw_time_sec', 'penalties_sec', 'final_time_sec', 'formatted_time'])
        except Exception as e:
            st.error(f"Error reading Relays from Google Sheets: {e}. Falling back to local data.")
            
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM relays ORDER BY final_time_sec ASC", conn)
    conn.close()
    return df

def write_individual_run(name, station, watch, category, age_group, raw_time, penalties, final_time):
    formatted_t = format_time(final_time)
    if APPS_SCRIPT_URL:
        try:
            payload = {
                "action": "add_individual",
                "name": name,
                "station": station,
                "watch": watch,
                "category": category,
                "age_group": age_group,
                "raw_time_sec": str(raw_time),
                "penalties_sec": str(penalties),
                "final_time_sec": str(final_time),
                "formatted_time": formatted_t
            }
            response = requests.post(APPS_SCRIPT_URL, data=payload, timeout=10)
            res_text = response.text
            if "SUCCESS" in res_text:
                st.session_state['last_sync_time'] = datetime.now()
                st.success(f"Successfully saved and synced {name}'s run to Google Sheets Cloud!")
                return True
            else:
                st.error(f"Apps Script Error: {res_text}")
        except Exception as e:
            st.error(f"Failed to write to Google Sheets: {e}. Attempting local database write...")

    # Fallback to Local
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO individuals (name, station, watch, category, age_group, raw_time_sec, penalties_sec, final_time_sec) VALUES (?,?,?,?,?,?,?,?)",
        (name, station, watch, category, age_group, raw_time, penalties, final_time)
    )
    conn.commit()
    conn.close()
    st.warning(f"⚠️ NOT SAVED TO CLOUD — logged locally only for {name}: {formatted_t}. Write this down on paper now as a backup.")
    return True

def write_relay_run(team_name, division, r1, r2, r3, r4, raw_time, penalties, final_time):
    formatted_t = format_time(final_time)
    if APPS_SCRIPT_URL:
        try:
            payload = {
                "action": "add_relay",
                "station": team_name, # Map team_name directly to the existing station column
                "relay_team_name": team_name, # Sheet header is relay_team_name
                "watch": "N/A", # Pass N/A for watches
                "division": division, # 'Male', 'Female', 'Mixed'
                "runner_1": r1,
                "runner_2": r2,
                "runner_3": r3,
                "runner_4": r4,
                "raw_time_sec": str(raw_time),
                "penalties_sec": str(penalties),
                "final_time_sec": str(final_time),
                "formatted_time": formatted_t
            }
            response = requests.post(APPS_SCRIPT_URL, data=payload, timeout=10)
            res_text = response.text
            if "SUCCESS" in res_text:
                st.session_state['last_sync_time'] = datetime.now()
                st.success(f"Successfully saved and synced {team_name} Relay run to Google Sheets Cloud!")
                return True
            else:
                st.error(f"Apps Script Error: {res_text}")
        except Exception as e:
            st.error(f"Failed to write to Google Sheets: {e}. Attempting local database write...")

    # Fallback to Local
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO relays (station, watch, division, runner_1, runner_2, runner_3, runner_4, raw_time_sec, penalties_sec, final_time_sec) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (team_name, "N/A", division, r1, r2, r3, r4, raw_time, penalties, final_time)
    )
    conn.commit()
    conn.close()
    st.warning(f"⚠️ NOT SAVED TO CLOUD — logged locally only for {team_name}: {formatted_t}. Write this down on paper now as a backup.")
    return True


# ==========================================================================
# BRAND HEADER BANNER
#
# Renders once, above the tabs, so it's the first thing on every tab (the
# tab content below is just whichever one is selected — this banner isn't
# per-tab, it's shared). Built as one flex-row HTML block with the crest
# base64-embedded, instead of st.columns + st.image + st.title, because
# st.columns stacks vertically on a phone — which is exactly what made the
# crest shrink down to a small icon sitting alone above the title instead
# of reading as a banner. A plain div with CSS flexbox keeps the crest and
# the title side by side at every width, with its own dark background so it
# reads as a header band rather than a logo someone happened to place near
# some text.
@st.cache_data
def _load_logo_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

_logo_b64 = _load_logo_b64("assets/nwfrs_logo.png")

st.markdown(f"""
<style>
.nwfc-header-banner {{
    display: flex;
    align-items: center;
    gap: clamp(10px, 3vw, 22px);
    background: linear-gradient(135deg, #1f2d3d 0%, #2c3e50 100%);
    border-radius: 12px;
    padding: clamp(10px, 2.5vw, 20px) clamp(14px, 3vw, 28px);
    margin-bottom: 14px;
}}
.nwfc-header-banner img {{
    height: clamp(48px, 11vw, 92px);
    width: clamp(48px, 11vw, 92px);
    object-fit: contain;
    flex-shrink: 0;
    background: #fff;
    border-radius: 8px;
    padding: 4px;
}}
.nwfc-header-banner .nwfc-header-text {{
    color: #ffffff;
    min-width: 0;
}}
.nwfc-header-banner .nwfc-header-title {{
    font-size: clamp(1.15rem, 4.2vw, 2.3rem);
    font-weight: 800;
    line-height: 1.2;
    margin: 0;
}}
.nwfc-header-banner .nwfc-header-sub {{
    font-size: clamp(0.78rem, 2.1vw, 1.05rem);
    color: #c7d0d9;
    margin: 2px 0 0 0;
}}
</style>
<div class="nwfc-header-banner">
    <img src="data:image/jpeg;base64,{_logo_b64}" alt="NWFRS crest" />
    <div class="nwfc-header-text">
        <p class="nwfc-header-title">🏆 NORTH WALES FIREFIGHTER CHALLENGE (NWFC)</p>
        <p class="nwfc-header-sub">Official Live Leaderboard &amp; Ticket Selection System</p>
    </div>
</div>
""", unsafe_allow_html=True)

# ==========================================================================
# PRINTABLE PDF GENERATION — paper backup forms & marshal observation sheets.
#
# Reads from the PENALTIES / COURSE_STATIONS lists defined above, so a
# change made there is reflected the next time a marshal downloads one of
# these from the Marshal Timer & Admin tab — there is no separate script
# to remember to re-run. reportlab is imported lazily inside these
# functions: if it's ever missing from the deployed environment, the rest
# of the app (leaderboard, ticket selection, entry forms) keeps working and
# only the download buttons show an error.
# ==========================================================================
AGE_BRACKETS_PDF = ['18-29', '30-34', '35-39', '40-44', '45-49', '50-54', '55+']


def _pdf_styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle('TitleNWFC', parent=base['Title'], fontSize=15, spaceAfter=2),
        "sub": ParagraphStyle('SubNWFC', parent=base['Normal'], fontSize=10,
                               textColor=colors.HexColor("#444444"), spaceAfter=6),
        "small": ParagraphStyle('SmallNWFC', parent=base['Normal'], fontSize=8.5, leading=11),
        "note": ParagraphStyle('NoteNWFC', parent=base['Normal'], fontSize=8,
                                textColor=colors.HexColor("#555555"), leading=10),
        "legend": ParagraphStyle('LegendNWFC', parent=base['Normal'], fontSize=8, leading=11),
        "cell": ParagraphStyle('CellNWFC', parent=base['Normal'], fontSize=8, leading=9.5),
        "h2": ParagraphStyle('H2NWFC', parent=base['Heading2'], fontSize=12, spaceBefore=6, spaceAfter=4),
    }


def _pdf_header_block(sty, subtitle_html, col_widths):
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle, Paragraph, Image
    logo = Image("assets/nwfrs_logo.png", width=18 * mm, height=18 * mm)
    text_cell = [
        Paragraph("NORTH WALES FIREFIGHTER CHALLENGE (NWFC)", sty["title"]),
        Paragraph(subtitle_html, sty["sub"]),
    ]
    t = Table([[logo, text_cell]], colWidths=col_widths)
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return t


def _pdf_penalty_legend_table(sty, penalty_rows, col_widths):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle, Paragraph
    data = [["Code", "Violation", "Penalty"]]
    for code, desc, label in penalty_rows:
        # Wrapped in a Paragraph so long violation text wraps within the column
        # instead of overflowing into the one next to it — plain strings in a
        # reportlab Table don't wrap on their own.
        data.append([code, Paragraph(desc, sty["cell"]), label])
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    return t


def _pdf_tally_table(sty, penalty_rows):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle, Paragraph
    header = ["Code", "Possible Penalty", "Pts", "Tally (tick each occurrence)", "Count", "Subtotal (s)"]
    data = [header]
    for code, desc, label in penalty_rows:
        data.append([code, Paragraph(desc, sty["cell"]), label, "", "", ""])
    t = Table(data, colWidths=[12 * mm, 70 * mm, 18 * mm, 37 * mm, 16 * mm, 24 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 1), (1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.6, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
        ('TOPPADDING', (0, 1), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
    ]))
    return t


def _pdf_signoff_block():
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle
    data = [
        ["Total Penalty Time (sum of Subtotal column):", "______________ seconds"],
        ["Official Stopwatch Time:", "Min ______  Sec.ms ______"],
        ["FINAL TIME (Stopwatch + Total Penalties):", "______________"],
    ]
    t = Table(data, colWidths=[95 * mm, 90 * mm])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (-1, -1), (-1, -1), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('LINEBELOW', (0, 0), (-1, -2), 0.4, colors.grey),
        ('BOX', (0, 0), (-1, -1), 0.8, colors.black),
    ]))
    return t


def _pdf_signature_block(rep_label="Competitor"):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle
    data = [
        ["Marshal / Referee Name (print):", "_____________________________"],
        ["Marshal / Referee Signature:", "_____________________________"],
        [f"{rep_label} Signature\n(confirms agreement with time & penalties above):", "_____________________________"],
        ["Date / Time:", "_____________________________"],
    ]
    t = Table(data, colWidths=[95 * mm, 90 * mm])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('LINEBELOW', (0, 0), (-1, -2), 0.4, colors.grey),
    ]))
    return t


def _chunk_list_to_text(items, per_line=9):
    lines = []
    for i in range(0, len(items), per_line):
        lines.append(", ".join(items[i:i + per_line]))
    return "<br/>".join(lines)


def build_paper_forms_pdf():
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

    sty = _pdf_styles()
    station_order_text = "  →  ".join(s["title"] for s in COURSE_STATIONS)
    ind_rows = [(p["code"], p["desc"], p["label"]) for p in PENALTIES if not p["relay_only"]]
    rel_rows = ind_rows + [(p["code"], p["desc"], p["label"]) for p in PENALTIES if p["relay_only"]]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        topMargin=12 * mm, bottomMargin=12 * mm, leftMargin=10 * mm, rightMargin=10 * mm,
    )
    story = []

    # ---- Page 1: Individual Entry Log (front) ----
    story.append(_pdf_header_block(
        sty,
        "PAPER BACKUP — Individual Competitor Log &nbsp;|&nbsp; "
        "Station: ____________________ &nbsp;&nbsp; Date: ____________ &nbsp;&nbsp; "
        "Marshal: ____________________ &nbsp;&nbsp; Sheet # ______",
        [24 * mm, 253 * mm],
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Fill in every column exactly as it would be entered on the web app. If the app or Wi-Fi is down, "
        "complete this sheet in full and upload/sync the entries once the system is back online. "
        "Reference codes (stations, watches, penalty codes) are printed on the reverse of this sheet.",
        sty["small"],
    ))
    story.append(Spacer(1, 6))

    headers = ["#", "Competitor Name", "Station", "Watch/Dept", "Cat.\n(OM/OF/NO)", "Age\nBracket",
               "Min", "Sec.ms", "Penalty\nCodes", "Final\nTime", "Marshal\nInitials"]
    col_widths = [8 * mm, 48 * mm, 32 * mm, 30 * mm, 22 * mm, 20 * mm,
                  14 * mm, 20 * mm, 26 * mm, 24 * mm, 22 * mm]
    data = [headers] + [[str(i), "", "", "", "", "", "", "", "", "", ""] for i in range(1, 16)]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.6, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
        ('TOPPADDING', (0, 1), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
    ]))
    story.append(t)

    # ---- Page 2: Individual reference codes (back) ----
    story.append(PageBreak())
    story.append(Paragraph("NWFC — Individual Log — Reference Codes (print on reverse of entry sheet)", sty["h2"]))
    story.append(Paragraph(
        "Category codes: OM = Operational Male &nbsp;|&nbsp; OF = Operational Female &nbsp;|&nbsp; "
        "NO = Non-Operational", sty["legend"]
    ))
    story.append(Paragraph("Age Brackets: " + ", ".join(AGE_BRACKETS_PDF), sty["legend"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph("<b>Course Station Order:</b> " + station_order_text, sty["legend"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph("<b>Penalty Codes (write applicable codes in the Penalty Codes column):</b>", sty["legend"]))
    story.append(_pdf_penalty_legend_table(sty, ind_rows, [16 * mm, 110 * mm, 28 * mm]))
    story.append(Spacer(1, 6))
    story.append(Paragraph("<b>Service Stations:</b> " + _chunk_list_to_text(STATIONS_LIST, per_line=9), sty["legend"]))
    story.append(Spacer(1, 3))
    story.append(Paragraph("<b>Watch / Dept / Sector:</b> " + _chunk_list_to_text(WATCHES_LIST, per_line=9), sty["legend"]))

    story.append(PageBreak())

    # ---- Page 3: Relay Entry Log (front) ----
    story.append(_pdf_header_block(
        sty,
        "PAPER BACKUP — Relay Team Log &nbsp;|&nbsp; "
        "Station: ____________________ &nbsp;&nbsp; Date: ____________ &nbsp;&nbsp; "
        "Marshal: ____________________ &nbsp;&nbsp; Sheet # ______",
        [24 * mm, 253 * mm],
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Fill in every column exactly as it would be entered on the web app. If the app or Wi-Fi is down, "
        "complete this sheet in full and upload/sync the entries once the system is back online. "
        "Reference codes are printed on the reverse of this sheet.",
        sty["small"],
    ))
    story.append(Spacer(1, 6))

    rel_headers = ["#", "Relay Team\nName", "Division\n(M/F/Mixed)", "Runner 1", "Runner 2", "Runner 3", "Runner 4",
                   "Min", "Sec.ms", "Penalty\nCodes", "Final\nTime", "Marshal\nInitials"]
    rel_col_widths = [8 * mm, 30 * mm, 20 * mm, 28 * mm, 28 * mm, 28 * mm, 28 * mm,
                       12 * mm, 18 * mm, 24 * mm, 22 * mm, 20 * mm]
    rel_data = [rel_headers] + [[str(i)] + [""] * 11 for i in range(1, 12)]
    rt = Table(rel_data, colWidths=rel_col_widths, repeatRows=1)
    rt.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.6, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
        ('TOPPADDING', (0, 1), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
    ]))
    story.append(rt)

    # ---- Page 4: Relay reference codes (back) ----
    story.append(PageBreak())
    story.append(Paragraph("NWFC — Relay Log — Reference Codes (print on reverse of entry sheet)", sty["h2"]))
    story.append(Paragraph("Division: Male / Female / Mixed", sty["legend"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph("<b>Course Station Order:</b> " + station_order_text, sty["legend"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph("<b>Penalty Codes (write applicable codes in the Penalty Codes column):</b>", sty["legend"]))
    story.append(_pdf_penalty_legend_table(sty, rel_rows, [16 * mm, 110 * mm, 28 * mm]))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


def build_observation_sheets_pdf():
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

    sty = _pdf_styles()
    station_order_text = "  →  ".join(s["title"] for s in COURSE_STATIONS)
    ind_penalties = [(p["code"], p["desc"], p["label"]) for p in PENALTIES if not p["relay_only"]]
    rel_penalties = ind_penalties + [(p["code"], p["desc"], p["label"]) for p in PENALTIES if p["relay_only"]]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=14 * mm, bottomMargin=14 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
    )
    story = []

    # ---- Individual Observation Sheet ----
    story.append(_pdf_header_block(
        sty, "Marshal Observation &amp; Penalty Sheet — Individual (one sheet per competitor run)",
        [22 * mm, 156 * mm],
    ))
    story.append(Spacer(1, 4))

    id_data = [
        ["Competitor Name:", "_____________________________", "Competitor No. (optional):", "____________"],
        ["Station:", "_____________________________", "Watch / Dept:", "____________________"],
        ["Age Bracket (circle one):", "  ".join(AGE_BRACKETS_PDF), "", ""],
        ["Category:", "Operational Male   /   Operational Female   /   Non-Operational", "", ""],
    ]
    id_table = Table(id_data, colWidths=[38 * mm, 78 * mm, 38 * mm, 36 * mm])
    id_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('SPAN', (1, 2), (3, 2)),
        ('SPAN', (1, 3), (3, 3)),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BOX', (0, 0), (-1, -1), 0.8, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.4, colors.grey),
    ]))
    story.append(id_table)
    story.append(Spacer(1, 6))

    story.append(Paragraph("Course order: " + station_order_text, sty["note"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "The observing marshal ticks a box in the Tally column each time a penalty is committed during the run. "
        "Multiple occurrences (e.g. RTC tools) get multiple ticks in the same row.",
        sty["note"],
    ))
    story.append(Spacer(1, 4))
    story.append(_pdf_tally_table(sty, ind_penalties))
    story.append(Spacer(1, 6))
    story.append(_pdf_signoff_block())
    story.append(Spacer(1, 6))
    story.append(_pdf_signature_block(rep_label="Competitor"))

    story.append(PageBreak())

    # ---- Relay Observation Sheet ----
    story.append(_pdf_header_block(
        sty, "Marshal Observation &amp; Penalty Sheet — Relay Team (one sheet per team run)",
        [22 * mm, 156 * mm],
    ))
    story.append(Spacer(1, 4))

    rel_id_data = [
        ["Relay Team Name:", "_____________________________", "Division:", "Male / Female / Mixed"],
        ["Runner 1:", "_____________________", "Runner 2:", "_____________________"],
        ["Runner 3:", "_____________________", "Runner 4:", "_____________________"],
        ["Station:", "_____________________", "Competitor No. (optional):", "____________"],
    ]
    rel_id_table = Table(rel_id_data, colWidths=[30 * mm, 68 * mm, 44 * mm, 48 * mm])
    rel_id_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BOX', (0, 0), (-1, -1), 0.8, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.4, colors.grey),
    ]))
    story.append(rel_id_table)
    story.append(Spacer(1, 6))

    story.append(Paragraph("Course order: " + station_order_text, sty["note"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "The observing marshal ticks a box in the Tally column each time a penalty is committed during the run. "
        "Multiple occurrences get multiple ticks in the same row.",
        sty["note"],
    ))
    story.append(Spacer(1, 4))
    story.append(_pdf_tally_table(sty, rel_penalties))
    story.append(Spacer(1, 6))
    story.append(_pdf_signoff_block())
    story.append(Spacer(1, 6))
    story.append(_pdf_signature_block(rep_label="Team Representative"))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


# Tab Layout
tab_leaderboard, tab_selection, tab_admin, tab_course = st.tabs([
    "📊 Live Standings",
    "🎟️ Swansea 2027 Ticket Selection",
    "⏱️ Marshal Timer & Admin",
    "📖 Competition Information"
])

with tab_leaderboard:
    st.markdown("### 🏆 Live Leaderboards (Updated Real-Time)")
    st.markdown("""
    <style>
    [data-testid="stTable"] { overflow-x: auto; }
    [data-testid="stTable"] table { width: 100%; }
    [data-testid="stTable"] th, [data-testid="stTable"] td {
        font-size: clamp(0.72rem, 3.3vw, 0.95rem);
        padding: 0.35rem 0.4rem;
    }
    </style>
    """, unsafe_allow_html=True)

    col_ind, col_rel = st.columns(2)

    with col_ind:
        st.markdown("#### 🏃 Individual Championship")

        # Side-by-side drop-down filters
        filt_c1, filt_c2 = st.columns(2)
        with filt_c1:
            category_filter = st.selectbox("Filter Individual Class:", [
                "All Operational Staff", "Operational Male Only", "Operational Female Only", "Non-Operational"
            ])
        with filt_c2:
            age_filter = st.selectbox("Filter Age Category:", [
                "All Age Groups", "18-29", "30-34", "35-39", "40-44", "45-49", "50-54", "55+"
            ])

        df_ind = get_individuals_data()

        if not df_ind.empty:
            if category_filter == "All Operational Staff":
                df_filtered = df_ind[df_ind['category'].isin(['Operational Male', 'Operational Female'])].copy()
            elif category_filter == "Operational Male Only":
                df_filtered = df_ind[df_ind['category'] == 'Operational Male'].copy()
            elif category_filter == "Operational Female Only":
                df_filtered = df_ind[df_ind['category'] == 'Operational Female'].copy()
            else:
                df_filtered = df_ind[df_ind['category'] == 'Non-Operational'].copy()

            if age_filter != "All Age Groups":
                df_filtered = df_filtered[df_filtered['age_group'] == age_filter].copy()

            if not df_filtered.empty:
                df_filtered = df_filtered.sort_values(by="final_time_sec", ascending=True)
                df_filtered["Time"] = df_filtered["final_time_sec"].apply(format_time)
                df_filtered.index = range(1, len(df_filtered) + 1)

                # Time sits right after the name so it is visible first on a phone.
                display_cols = ["name", "Time", "age_group", "station", "watch"]
                if "category" in df_filtered.columns and category_filter == "All Operational Staff":
                    display_cols.insert(2, "category")
                _tbl = df_filtered[display_cols].rename(columns={
                    "name": "Name", "category": "Category", "age_group": "Age",
                    "station": "Station", "watch": "Watch"})
                _tbl = _tbl.reset_index(drop=True)
                _tbl.insert(0, "Pos", [str(i) for i in range(1, len(_tbl) + 1)])
                _tbl.index = [""] * len(_tbl)  # blank index so numbers are not doubled
                st.table(_tbl)
            else:
                st.info("No runs recorded in this filtered category yet.")
        else:
            st.info("No runs recorded in this category yet.")

    with col_rel:
        st.markdown("#### 👥 Service Relays")
        division_filter = st.selectbox("Filter Relay Class:", [
            "All Relay Teams", "Male", "Female", "Mixed"
        ])

        df_rel = get_relays_data()

        if not df_rel.empty:
            if division_filter == "All Relay Teams":
                df_filtered_rel = df_rel.copy()
            else:
                df_filtered_rel = df_rel[df_rel['division'] == division_filter].copy()

            if not df_filtered_rel.empty:
                df_filtered_rel = df_filtered_rel.sort_values(by="final_time_sec", ascending=True)
                df_filtered_rel["Time"] = df_filtered_rel["final_time_sec"].apply(format_time)
                df_filtered_rel.index = range(1, len(df_filtered_rel) + 1)

                # The Sheet header for the team name can differ from the local DB
                # ("station"), so match any sensible variant instead of dropping the column.
                df_display = df_filtered_rel.copy()
                _team_aliases = ("relay_team_name", "station", "team", "team name", "team_name",
                                 "relay team name", "relay name", "relay_name", "relay")
                _team_col = next((c for c in df_display.columns
                                  if str(c).strip().lower() in _team_aliases), None)
                if _team_col is not None:
                    df_display = df_display.rename(columns={_team_col: "Relay Team Name"})
                else:
                    st.warning("Relay team name column not found in the data. Columns available: "
                               + ", ".join(str(c) for c in df_display.columns))

                cols_to_show = ["Relay Team Name", "Time", "division", "runner_1", "runner_2", "runner_3", "runner_4"]
                cols_to_show = [c for c in cols_to_show if c in df_display.columns]

                _rel_tbl = df_display[cols_to_show].rename(columns={
                    "Relay Team Name": "Relay Team", "division": "Division",
                    "runner_1": "Runner 1", "runner_2": "Runner 2",
                    "runner_3": "Runner 3", "runner_4": "Runner 4"})
                _rel_tbl = _rel_tbl.reset_index(drop=True)
                _rel_tbl.insert(0, "Pos", [str(i) for i in range(1, len(_rel_tbl) + 1)])
                _rel_tbl.index = [""] * len(_rel_tbl)  # blank index so numbers are not doubled
                st.table(_rel_tbl)
            else:
                st.info("No relay times recorded in this filtered category yet.")
        else:
            st.info("No relay times recorded in this category yet.")

with tab_selection:
    st.markdown("### 🎟️ Welsh Firefighter Challenge Ticket Allocation")
    st.markdown("#### Road to Swansea 2027")    
    st.write(
        "<b>Selection Criteria:</b> 16 Tickets total, including entry, hotel, breakfast, and transport. "
        "Guaranteed tickets are awarded to the <b>Top 4 Males</b> and <b>Top 4 Females</b>. "
        "The remaining 8 tickets are split evenly, <b>4 male and 4 female</b>, with each gender's 4 spread "
        "proportionately across active age categories depending on where that gender's top 4 fall. "
        "This gives 8 male and 8 female places in total.",
        unsafe_allow_html=True
    )
    
    df_all_ind = get_individuals_data()
    
    males = pd.DataFrame()
    females = pd.DataFrame()
    
    if not df_all_ind.empty:
        males = df_all_ind[df_all_ind['category'] == 'Operational Male'].sort_values(by="final_time_sec", ascending=True)
        females = df_all_ind[df_all_ind['category'] == 'Operational Female'].sort_values(by="final_time_sec", ascending=True)
    
    col_sel_m, col_sel_f = st.columns(2)
    
    with col_sel_m:
        st.markdown("##### 🟢 Guaranteed Male Selection (Top 4)")
        if len(males) >= 4:
            top_m = males.head(4).copy()
            top_m["Time"] = top_m["final_time_sec"].apply(format_time)
            # males/females keep their original row index from df_all_ind after
            # the filter+sort above (pandas doesn't renumber on sort_values), so
            # without this st.table shows those leftover index numbers in the
            # first column instead of a clean 1-4 rank — same fix already
            # applied to the two leaderboard tables above.
            top_m.index = range(1, len(top_m) + 1)
            st.table(top_m[["name", "age_group", "Time"]])
        else:
            st.warning("Need at least 4 operational male runs to populate.")
            
    with col_sel_f:
        st.markdown("##### 🔴 Guaranteed Female Selection (Top 4)")
        if len(females) >= 4:
            top_f = females.head(4).copy()
            top_f["Time"] = top_f["final_time_sec"].apply(format_time)
            top_f.index = range(1, len(top_f) + 1)
            st.table(top_f[["name", "age_group", "Time"]])
        else:
            st.warning("Need at least 4 operational female runs to populate.")
            
        # Proportional Allocation logic (4 male + 4 female remaining places)
    st.markdown("#### 🎯 Proportional Remaining 8-Ticket Distribution")
    all_ages = ['18-29', '30-34', '35-39', '40-44', '45-49', '50-54', '55+']
    REMAINING_PER_GENDER = 4

    def _apportion(weights, avail, total):
        """Largest-remainder split of `total` tickets across brackets, capped by who is available."""
        wsum = sum(weights.values())
        if wsum == 0:
            return {a: 0 for a in weights}
        raw = {a: weights[a] / wsum * total for a in weights}
        quota = {a: int(raw[a]) for a in weights}
        short = total - sum(quota.values())
        for a in sorted(weights, key=lambda a: raw[a] - quota[a], reverse=True)[:short]:
            quota[a] += 1
        leftover = 0
        for a in weights:
            if quota[a] > avail[a]:
                leftover += quota[a] - avail[a]
                quota[a] = avail[a]
        while leftover > 0:
            spare = [a for a in weights if quota[a] < avail[a]]
            if not spare:
                break
            spare.sort(key=lambda a: weights[a], reverse=True)
            quota[spare[0]] += 1
            leftover -= 1
        return quota

    if len(males) >= 4 and len(females) >= 4:
        top_8 = pd.concat([males.head(4), females.head(4)])
        guaranteed_names = set(top_8['name'])
        top_8_ages = list(top_8['age_group'])

        st.write("<b>Age Brackets represented in Guaranteed Top 8:</b>", unsafe_allow_html=True)
        cols = st.columns(len(all_ages))
        for idx, age in enumerate(all_ages):
            with cols[idx]:
                st.metric(label=f"Bracket {age}", value=top_8_ages.count(age))

        def _remaining_for(gender_df):
            guaranteed_df = gender_df.head(4)
            pool_g = gender_df[~gender_df['name'].isin(guaranteed_names)].copy()
            g_counts = {a: int((guaranteed_df['age_group'] == a).sum()) for a in all_ages}
            avail = {a: int((pool_g['age_group'] == a).sum()) for a in all_ages}
            # Brackets already well represented in this gender's top 4 are weighted down
            weights = {a: max(0, avail[a] - g_counts[a]) for a in all_ages}
            if sum(weights.values()) == 0:
                weights = dict(avail)
            quota = _apportion(weights, avail, REMAINING_PER_GENDER)
            parts = [pool_g[pool_g['age_group'] == a].sort_values(by="final_time_sec", ascending=True).head(quota[a])
                     for a in all_ages if quota[a] > 0]
            picked = pd.concat(parts).sort_values(by="final_time_sec", ascending=True) if parts else pool_g.head(0)
            picked = picked.copy()
            picked["Time"] = picked["final_time_sec"].apply(format_time)
            picked.index = range(1, len(picked) + 1)
            return picked, quota

        rem_m, quota_m = _remaining_for(males)
        rem_f, quota_f = _remaining_for(females)

        col_rm, col_rf = st.columns(2)
        with col_rm:
            st.markdown("##### 🟢 Remaining 4 Male Tickets")
            st.caption("Quota by bracket: " + ", ".join(f"{a}: {quota_m[a]}" for a in all_ages if quota_m[a] > 0))
            if len(rem_m) < REMAINING_PER_GENDER:
                st.warning(f"Only {len(rem_m)} eligible male competitors available for these 4 tickets.")
            if len(rem_m):
                st.table(rem_m[["name", "age_group", "Time"]])
        with col_rf:
            st.markdown("##### 🔴 Remaining 4 Female Tickets")
            st.caption("Quota by bracket: " + ", ".join(f"{a}: {quota_f[a]}" for a in all_ages if quota_f[a] > 0))
            if len(rem_f) < REMAINING_PER_GENDER:
                st.warning(f"Only {len(rem_f)} eligible female competitors available for these 4 tickets.")
            if len(rem_f):
                st.table(rem_f[["name", "age_group", "Time"]])

        st.markdown("---")
        st.write("<b>🏆 Full 16-Ticket Roster (Guaranteed 8 + Proportional 8):</b>", unsafe_allow_html=True)
        full_roster = pd.concat([top_8, rem_m, rem_f]).sort_values(by="final_time_sec", ascending=True).copy()
        full_roster["Time"] = full_roster["final_time_sec"].apply(format_time)
        full_roster.index = range(1, len(full_roster) + 1)
        st.table(full_roster[["name", "category", "age_group", "Time"]])

        n_m = int((full_roster["category"] == "Operational Male").sum())
        n_f = int((full_roster["category"] == "Operational Female").sum())
        if n_m == 8 and n_f == 8:
            st.success("✅ Roster check: 8 male + 8 female = 16 tickets.")
        else:
            st.warning(f"⚠️ Roster check: {n_m} male + {n_f} female. Not enough eligible competitors yet to fill 8 of each.")
def _check_admin_pw():
    """Runs when the password box is submitted: verify once, then wipe the box."""
    entered = st.session_state.get("admin_pw_input", "")
    admin_pw = st.secrets.get("ADMIN_PASSWORD", "")
    if admin_pw and entered == admin_pw:
        st.session_state["admin_authed"] = True
        st.session_state["admin_pw_wrong"] = False
    else:
        st.session_state["admin_pw_wrong"] = bool(entered)
    st.session_state["admin_pw_input"] = ""   # never leave the typed password sitting in the box

def _admin_logout():
    st.session_state["admin_authed"] = False
    st.session_state["admin_pw_wrong"] = False
with tab_admin:
    st.markdown("### ⏱️ Marshal Race Time Recording")
    if not st.session_state.get("admin_authed", False):
        st.text_input("Enter Admin Password:", type="password", key="admin_pw_input", on_change=_check_admin_pw)
        if st.session_state.get("admin_pw_wrong"):
            st.error("Incorrect password.")
    if st.session_state.get("admin_authed", False):
        st.success("Access Granted. Marshal Timing Form Active.") 
        st.button("🔒 Log out of marshal panel", on_click=_admin_logout)

        # Sync health indicator — visible at all times so marshals know whether
        # the cloud connection is currently working, without having to remember
        # the last error message they saw.
        last_sync = st.session_state.get('last_sync_time')
        if not APPS_SCRIPT_URL:
            st.warning("🔌 No Apps Script URL configured — all entries are going to local storage only, which does not survive an app restart. Paper backup required.")
        elif last_sync is None:
            st.info("☁️ Cloud sync status: no successful sync yet this session.")
        else:
            st.info(f"☁️ Last successful cloud sync: {last_sync.strftime('%H:%M:%S')}")

        # Printable paper backups — generated on demand from the same PENALTIES /
        # COURSE_STATIONS lists the rest of this app uses, so what a marshal prints
        # today always matches what's live in the app right now.
        with st.expander("🖨️ Printable Backup Documents (Marshal Only)"):
            st.caption(
                "These PDFs are built fresh from this app's own penalty and station list each time you "
                "download, so they can't go out of date the way a separately-maintained file could."
            )
            try:
                st.download_button(
                    "📄 Download Paper Backup Forms (Individual + Relay log sheets)",
                    data=build_paper_forms_pdf(),
                    file_name="NWFC_Paper_Backup_Forms.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    key="dl_paper_forms",
                )
            except Exception as e:
                st.error(f"Could not generate Paper Backup Forms PDF: {e}")

            try:
                st.download_button(
                    "📄 Download Marshal Observation & Penalty Sheets",
                    data=build_observation_sheets_pdf(),
                    file_name="NWFC_Marshal_Observation_Sheets.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    key="dl_observation_sheets",
                )
            except Exception as e:
                st.error(f"Could not generate Marshal Observation Sheets PDF: {e}")

        # Cloud backend configuration — marshal-only. These fields are pre-filled
        # from Streamlit secrets and normally never need touching; they're here
        # purely as a manual override for the current browser session, kept out
        # of public view entirely.
        with st.expander("⚙️ Cloud Backend Configuration (Marshal Only — overrides this session only)"):
            st.text_input(
                "Google Sheet Individuals CSV URL:",
                key="gsheet_individuals_csv",
                help="Paste the web-published CSV link or export link of your Google Sheet for individuals."
            )
            st.text_input(
                "Google Sheet Relays CSV URL:",
                key="gsheet_relays_csv",
                help="Paste the web-published CSV link or export link of your Google Sheet for relays."
            )
            st.text_input(
                "Google Apps Script Web App URL:",
                key="apps_script_url",
                type="password",
                help="Paste the deployed Google Apps Script Web App URL to enable writing directly to your Google Sheet."
            )
            st.caption("Changes here apply only to your current browser session. To make a change permanent for everyone, update it in the app's Streamlit secrets instead.")

        # One-time setup reference — collapsed by default and only ever shown to an
        # authenticated marshal. This has nothing to do with running the event day to
        # day; it's historical setup documentation from when the Google Sheets backend
        # was first configured, kept here for reference rather than deleted outright.
        with st.expander("📂 Google Sheets Cloud Backend — Setup Reference (rarely needed)"):
            st.write(
                "This was used to configure the Google Sheets cloud backend when it was first set up. "
                "You shouldn't need this during normal event operation — it's kept here only in case the "
                "backend ever needs to be reconfigured or rebuilt from scratch."
            )
            st.markdown("""
            1. **Create and Share your Google Sheet (No Technical "Publish to Web" Needed!):**
               * Create a standard Google Sheet with two tabs: `individuals` and `relays`.
               * Create the header row in `individuals`: `id`, `name`, `station`, `watch`, `category`, `age_group`, `raw_time_sec`, `penalties_sec`, `final_time_sec`, `formatted_time`.
               * Create the header row in `relays`: `id`, `station`, `watch`, `division`, `runner_1`, `runner_2`, `runner_3`, `runner_4`, `raw_time_sec`, `penalties_sec`, `final_time_sec`, `formatted_time`.
               * Click the blue **Share** button in the top-right corner of Google Sheets. Under **General access**, change it from "Restricted" to **Anyone with the link can view** (this allows the app to read your live data).
               * Simply copy the **standard URL from your Chrome address bar** for each tab!
                 * For the `individuals` tab, copy the link and paste it into **Google Sheet Individuals CSV URL** in the Cloud Backend Configuration section above.
                 * For the `relays` tab, copy the link and paste it into **Google Sheet Relays CSV URL** in the Cloud Backend Configuration section above.
                 * *The app will automatically and instantly convert these into high-performance, real-time export links with ZERO sync delay!*
            2. **Create the Write Apps Script:**
               * In your Google Sheet, go to **Extensions > Apps Script**.
               * Paste the following lightweight, secure code:
                 ```javascript
                 function doPost(e) {
                   var action = e.parameter.action;
                   var sheetName = (action === "add_individual") ? "individuals" : "relays";
                   var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(sheetName);
                   if (!sheet) {
                     return ContentService.createTextOutput("ERROR: Sheet not found");
                   }
                   var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
                   var nextId = sheet.getLastRow();
                   var newRow = headers.map(function(h) {
                     if (h === "id") return nextId;
                     return e.parameter[h] || "";
                   });
                   sheet.appendRow(newRow);
                   return ContentService.createTextOutput("SUCCESS");
                 }
                 ```
               * Click **Deploy > New Deployment**. Choose **Web App**.
               * Set **Execute as:** *Me*, and **Who has access:** *Anyone*.
               * Copy the generated **Web App URL** and paste it into **Google Apps Script Web App URL** in the Cloud Backend Configuration section above.
            3. **Enjoy Zero Data Loss:**
               * Once these are set, the app will securely read and write directly to your cloud sheet. Closing the app, browser, or signing out will never wipe your data!
            """)

        # Initialise session state to track the active form if not present
        if 'admin_mode' not in st.session_state:
            st.session_state.admin_mode = "individual"
            
        st.write("##### 🎛️ Select Form Type:")
        # Separated on either side of the page with a clear gap in between
        col_btn_l, col_spacer, col_btn_r = st.columns([3, 1, 3])
        with col_btn_l:
            st.markdown("<p style='text-align: center; font-size: 24px; margin-bottom: 0px;'>🏃</p>", unsafe_allow_html=True)
            if st.button("LOG INDIVIDUAL COMPETITOR TIME", use_container_width=True, type="primary" if st.session_state.admin_mode == "individual" else "secondary"):
                st.session_state.admin_mode = "individual"
                st.rerun()
        with col_btn_r:
            st.markdown("<p style='text-align: center; font-size: 24px; margin-bottom: 0px;'>👥</p>", unsafe_allow_html=True)
            if st.button("LOG RELAY CHAMPIONSHIP TEAM TIME", use_container_width=True, type="primary" if st.session_state.admin_mode == "relay" else "secondary"):
                st.session_state.admin_mode = "relay"
                st.rerun()
                
        st.markdown("---")
        
        if st.session_state.admin_mode == "individual":
            st.markdown("#### 🏃 Individual Competitor Entry Form")
            st.info("💡 Note: Keyboard shortcut 'Enter to submit' has been removed to prevent accidental entries. All fields start blank and the submit button unlocks automatically once all required fields are complete.")
            
            name = st.text_input("Competitor Name:", value="", placeholder="Enter full name...", key="ind_name")
            station = st.selectbox("Station:", STATIONS_LIST, index=None, placeholder="Select Station...", key="ind_station")
            watch = st.selectbox("Watch / Dept / Sector:", WATCHES_LIST, index=None, placeholder="Select Watch / Dept...", key="ind_watch")
            category = st.selectbox("Class Category:", ["Operational Male", "Operational Female", "Non-Operational"], index=None, placeholder="Select Class Category...", key="ind_cat")
            age_group = st.selectbox("Age Bracket:", ['18-29', '30-34', '35-39', '40-44', '45-49', '50-54', '55+'], index=None, placeholder="Select Age Bracket...", key="ind_age")
            
            st.markdown("##### ⏱️ Raw Stopwatch Time")
            col_m, col_s = st.columns(2)
            with col_m:
                mins = st.number_input("Minutes:", min_value=0, max_value=10, value=0, step=1, key="ind_mins")
            with col_s:
                secs = st.number_input("Seconds (and ms):", min_value=0.0, max_value=59.999, value=0.0, step=1.0, format="%.3f", key="ind_secs")
            
            st.markdown("##### ⚠️ Rule Violations & Penalties")
            penalties = 0
            for p in PENALTIES:
                if p["relay_only"]:
                    continue
                if st.checkbox(f"{p['desc']} ({p['label']})", key=f"ind_{p['code']}"):
                    penalties += p["pts"]
            
            # Validation check
            ind_time_valid = (mins is not None or secs is not None) and ((mins or 0) * 60 + (secs or 0.0) > 0)
            ind_ready = bool(name and name.strip() and station and watch and category and age_group and ind_time_valid)
            
            st.markdown("<br>", unsafe_allow_html=True)
            if not ind_ready:
                missing = []
                if not (name and name.strip()): missing.append("Competitor Name")
                if not station: missing.append("Station")
                if not watch: missing.append("Watch/Dept")
                if not category: missing.append("Category")
                if not age_group: missing.append("Age Bracket")
                if not ind_time_valid: missing.append("Stopwatch Time > 0")
                st.warning(f"🔒 Submission Restricted: Please complete the following required fields to enable the submit button: **{', '.join(missing)}**.")
                st.button("Log Run and Sync Leaderboard", disabled=True, use_container_width=True, key="ind_sub_disabled")
            else:
                if st.button("🚀 LOG INDIVIDUAL RUN AND SYNC LEADERBOARD", type="primary", use_container_width=True, key="ind_sub_enabled"):
                    raw_tot = (mins or 0) * 60 + (secs or 0.0)
                    final_tot = raw_tot + penalties
                    write_individual_run(name, station, watch, category, age_group, raw_tot, penalties, final_tot)
                    
        else:
            st.markdown("#### 👥 Relay Team Entry Form")
            st.info("💡 Note: Keyboard shortcut 'Enter to submit' has been removed to prevent accidental entries. All fields start blank and the submit button unlocks automatically once all required fields are complete.")
            
            relay_team_name = st.text_input("Relay Team Name:", value="", placeholder="Enter team name...", key="rel_team")
            division = st.selectbox("Relay Division:", ["Male", "Female", "Mixed"], index=None, placeholder="Select Relay Division...", key="rel_div")
            
            st.markdown("##### 🏃 Running Order (4-Person Team)")
            r1 = st.text_input("Runner 1 (Shuttle & RTC):", value="", placeholder="Runner 1 full name...", key="rel_r1")
            r2 = st.text_input("Runner 2 (Force & Drag):", value="", placeholder="Runner 2 full name...", key="rel_r2")
            r3 = st.text_input("Runner 3 (Makeup & Foam):", value="", placeholder="Runner 3 full name...", key="rel_r3")
            r4 = st.text_input("Runner 4 (Dummy Rescue):", value="", placeholder="Runner 4 full name...", key="rel_r4")
            
            st.markdown("##### ⏱️ Raw Relay Stopwatch Time")
            col_m, col_s = st.columns(2)
            with col_m:
                mins = st.number_input("Relay Minutes:", min_value=0, max_value=10, value=0, step=1, key="rel_mins")
            with col_s:
                secs = st.number_input("Relay Seconds (and ms):", min_value=0.0, max_value=59.999, value=0.0, step=1.0, format="%.3f", key="rel_secs")
            
            st.markdown("##### ⚠️ Rule Violations & Penalties")
            penalties = 0
            for p in PENALTIES:
                if st.checkbox(f"{p['desc']} ({p['label']})", key=f"rel_{p['code']}"):
                    penalties += p["pts"]
            
            # Validation check
            rel_time_valid = (mins is not None or secs is not None) and ((mins or 0) * 60 + (secs or 0.0) > 0)
            rel_ready = bool(relay_team_name and relay_team_name.strip() and division and r1 and r1.strip() and r2 and r2.strip() and r3 and r3.strip() and r4 and r4.strip() and rel_time_valid)
            
            st.markdown("<br>", unsafe_allow_html=True)
            if not rel_ready:
                missing = []
                if not (relay_team_name and relay_team_name.strip()): missing.append("Relay Team Name")
                if not division: missing.append("Division")
                if not (r1 and r1.strip()): missing.append("Runner 1")
                if not (r2 and r2.strip()): missing.append("Runner 2")
                if not (r3 and r3.strip()): missing.append("Runner 3")
                if not (r4 and r4.strip()): missing.append("Runner 4")
                if not rel_time_valid: missing.append("Stopwatch Time > 0")
                st.warning(f"🔒 Submission Restricted: Please complete the following required fields to enable the submit button: **{', '.join(missing)}**.")
                st.button("Log Relay Team and Sync Leaderboard", disabled=True, use_container_width=True, key="rel_sub_disabled")
            else:
                if st.button("🚀 LOG RELAY TEAM AND SYNC LEADERBOARD", type="primary", use_container_width=True, key="rel_sub_enabled"):
                    raw_tot = (mins or 0) * 60 + (secs or 0.0)
                    final_tot = raw_tot + penalties
                    write_relay_run(relay_team_name, division, r1, r2, r3, r4, raw_tot, penalties, final_tot)

    else:
        st.info("Enter password '*******' in the field above to activate the marshal logger panel.")

with tab_course:
    st.markdown("### 📖 Competition Information")
    st.write(
        "Everything you need to know before you compete: the official course layout, a station-by-station "
        "breakdown of every obstacle, what's expected of individual and relay competitors, and the full "
        "rules and penalties reference. Video walkthroughs will appear here as they're added."
    )

    info_overview, info_stations, info_individual, info_relay, info_rules = st.tabs([
        "🗺️ Course Overview",
        "🏗️ Station-by-Station Guide",
        "🏃 Individual Event",
        "👥 Relay Event",
        "⚠️ Rules & Penalties",
    ])

    # ---------------- Course Overview ----------------
    with info_overview:
        st.markdown("#### Official Top-Down Course Layout")
        st.write(
            "The official, single-lane, vertical track layout. Designed with compact station-yard boundaries.",
            unsafe_allow_html=True
        )
        st.image(
            "assets/nwfc_course_blueprint_topdown.png",
            caption="Official Course Layout",
            use_container_width=True,
        )

        st.markdown("---")
        st.markdown("##### 🎥 Full Course Walkthrough")
        # Paste a YouTube, Vimeo, or direct video file URL here once recorded.
        FULL_COURSE_VIDEO_URL = "https://youtu.be/8e4w8Q7ivGY"
        if FULL_COURSE_VIDEO_URL:
            st.video(FULL_COURSE_VIDEO_URL)
        else:
            st.info("🎥 Full course walkthrough video coming soon.")

    # ---------------- Station-by-Station Guide ----------------
    with info_stations:
        st.markdown("#### Station-by-Station Guide")
        st.caption(
            "⚠️ Editor's note: the apparatus names come straight from the single penalty/station list this "
            "whole app shares (same source as the entry forms and the Rules & Penalties table below — "
            "so it cannot drift out of sync with them). The bracketed [ ] technique descriptions are still "
            "placeholders only — replace them with the exact wording from your rulebook before publishing "
            "this page. Claude has not seen the rulebook and has not verified the actual technique requirements."
        )

        # Paste a video URL per station as footage becomes available.
        STATION_VIDEOS = {
            "shuttle": "",
            "cleveland_carry": "https://youtu.be/u5qpXoFd2Tw",
            "rtc_carry": "https://youtu.be/bZkXUl4mGRE",
            "force_machine": "https://youtu.be/Ff7ownU6beg",
            "hose_drag": "https://youtu.be/NVcwHFy3O6k",
            "hose_makeup": "https://youtu.be/rhBv7v0QONg",
            "containers": "https://youtu.be/h7T7LXDG1Gs",
            "dummy_drag": "https://youtu.be/R7gZ2GwpUWY",
        }

        for s in COURSE_STATIONS:
            with st.expander(s["title"]):
                col_desc, col_video = st.columns([3, 2])
                with col_desc:
                    st.markdown(f"**What to do:** {s['desc']}")
                    st.markdown(f"**Acceptable / Not acceptable:** {s['acceptable']}")
                    st.markdown(f"**Penalty if breached:** {station_penalty_text(s['key'])}")
                with col_video:
                    video_url = STATION_VIDEOS.get(s["key"], "")
                    if video_url:
                        st.video(video_url)
                    else:
                        st.info("🎥 Video coming soon")

        st.markdown("---")
        st.caption(
            "Relay-only penalties (Relay Touch-Tag Missed or Out of Zone, Dummy Drag Boundary Lane Crossing) "
            "are covered in the Relay Event and Rules & Penalties tabs."
        )

        # ---------------- Individual Event ----------------
    with info_individual:
        st.markdown("#### Individual Event Format")
        INDIVIDUAL_STEPS = [
            "Competitor begins the run on the start line with hand placed on the RTC Tool Table.",
            "**Station 1 - 35m Shuttle** - Run the length of the course to the Cleveland Roll.",
            "**Station 2 - Cleveland Roll** - Pick up the Cleveland Roll and run back down the course. Place the Cleveland Roll on top of the Crash Mat.",
            "**Station 3 - RTC Tool Carry** - Pick up an RTC Tool and take it to the RTC Tool Table at the start line of the course. Go back and collect the 2nd Tool and repeat.",
            "**Station 4 - Force Entry Machine** - Step onto the Force Entry Machine, pick up the hammer and strike the plate until you see a green marker. Place the hammer on the mat.",
            "**Station 5 - 35m Hose Drag** - Pick up the mainline branch and run the length of the course, placing it over the line and on the mat.",
            "**Station 6 - Hose Make-Up** - Run back down the course to the lay flat hose, make it up and place in the Hose Box.",
            "**Station 7 - Container Carry** - Grab 2 containers, transport to the empty tray, run back for the remaining 2 and repeat.",
            "**Station 8 - Dummy Drag** - Run back down the course, pick up the dummy, and drag 25m to the hose box, turn and drag to the finish line.",
        ]
        st.markdown("\n\n".join(INDIVIDUAL_STEPS))
        INDIVIDUAL_VIDEO_URL = ""
        if INDIVIDUAL_VIDEO_URL:
            st.video(INDIVIDUAL_VIDEO_URL)
        else:
            st.info("🎥 Individual full run-through video coming soon.")

        # ---------------- Relay Event ----------------
    with info_relay:
        st.markdown("#### Relay Event Format")
        st.write(
            "A relay team is four runners. Each runner takes a specific leg of the course — these are the "
            "same roles marshals select on the Relay Team Entry Form, so the labels here match exactly what "
            "appears when a relay time is logged."
        )
        RELAY_VIDEOS = {
            "r1": "",
            "r2": "",
            "r3": "",
            "r4": "",
        }
        relay_legs = [
            ("r1", "Runner 1 — Shuttle & RTC",
             "Runner 1 at the start line, runs the 35m Shuttle, picks up the Cleveland Roll, runs it back and places it on the crash mat. Completes the RTC Tool Carry and then tags Runner 2"),
            ("r2", "Runner 2 — Force & Drag",
             "Runner 2 will start next to the RTC Tool Table, once tagged will complete the Force Entry Machine, move to the Hose Drag, place it on the mat and tag Runner 3"),
            ("r3", "Runner 3 — Makeup & Foam",
             "Runner 3 will be waiting next to the hose drag mat, once tagged will run back down the course to the hose makeup, make up the hose, place in the box, complete the container carry and then tag Runner 4"),
            ("r4", "Runner 4 — Dummy Rescue",
             "Runner 4 will be waiting next to the container trays, once tagged will run back down the course to the dummy, pick up and carry out the 25m dummy drag to the finish"),
        ]
        for key, leg_name, desc in relay_legs:
            with st.expander(leg_name):
                st.write(desc)
                video_url = RELAY_VIDEOS.get(key, "")
                if video_url:
                    st.video(video_url)
                else:
                    st.info("🎥 Video coming soon")

        # ---------------- Rules & Penalties ----------------
    with info_rules:
        st.markdown("#### Full Rules & Penalties Reference")
        st.write("The following penalties apply to both individual and relay entries:")
        common_penalties = pd.DataFrame(
            [(p["desc"], p["label"]) for p in PENALTIES if not p["relay_only"]],
            columns=["Violation", "Penalty"],
        )
        st.table(common_penalties)

        st.write("These additional penalties apply to relay entries only:")
        relay_penalties = pd.DataFrame(
            [(p["desc"], p["label"]) for p in PENALTIES if p["relay_only"]],
            columns=["Violation", "Penalty"],
        )
        st.table(relay_penalties)

        st.caption(
            "This table, the Marshal entry-form checkboxes, and the Station-by-Station Guide above all read "
            "from the same penalty list in the app's code — there is nothing left to keep in sync by hand."
        )
