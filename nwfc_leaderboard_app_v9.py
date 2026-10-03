import streamlit as st
import pandas as pd
import sqlite3
import os
import urllib.request
import urllib.parse
import csv
import io
import json
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
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==========================================
# MOBILE / ACCESSIBILITY CSS
# ==========================================
# Confirmed via a real phone recording (1080x2400): without this, the hero
# title wraps across 4 lines and pushes the leaderboard off the first screen,
# and the tab bar needs horizontal scrolling sooner than it should.
st.markdown("""
<style>
/* Hero title: scales with viewport width so it stops eating the whole
   first screen on a phone, but stays large on desktop. */
h1 {
    font-size: clamp(1.4rem, 5vw, 2.75rem) !important;
    line-height: 1.2 !important;
}

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


st.title("🏆 NORTH WALES FIREFIGHTER CHALLENGE (NWFC)")
st.subheader("Official Live Leaderboard & Ticket Selection System")

# Tab Layout
tab_leaderboard, tab_selection, tab_admin, tab_course = st.tabs([
    "📊 Live Standings",
    "🎟️ Swansea 2027 Ticket Selection",
    "⏱️ Marshal Timer & Admin",
    "📖 Competition Information"
])

with tab_leaderboard:
    st.markdown("### 🏆 Live Leaderboards (Updated Real-Time)")
    
    col_ind, col_rel = st.columns(2)
    
    with col_ind:
        st.markdown("#### 🏃 Individual Championship")
        
        # Symmetrical and professional side-by-side drop-down filters
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
                
            # Direct age filtering integration
            if age_filter != "All Age Groups":
                df_filtered = df_filtered[df_filtered['age_group'] == age_filter].copy()
                
            if not df_filtered.empty:
                df_filtered = df_filtered.sort_values(by="final_time_sec", ascending=True)
                df_filtered["Time"] = df_filtered["final_time_sec"].apply(format_time)
                df_filtered.index = range(1, len(df_filtered) + 1)
                
                # Dynamic visual columns based on selection. Time is placed
                # right after the name (rather than last) so it's the first
                # thing visible on a phone, before anyone has to scroll the
                # table sideways past station/watch/category/age_group —
                # confirmed via a real phone test that Time was being pushed
                # off-screen entirely in the old column order.
                display_cols = ["name", "Time", "age_group", "station", "watch"]
                if "category" in df_filtered.columns and category_filter == "All Operational Staff":
                    display_cols.insert(2, "category")
                st.dataframe(
                    df_filtered[display_cols],
                    use_container_width=True,
                    column_config={
                        "name": st.column_config.TextColumn("Name", width="medium"),
                        "Time": st.column_config.TextColumn("Time", width="small"),
                        "category": st.column_config.TextColumn("Category", width="small"),
                        "age_group": st.column_config.TextColumn("Age", width="small"),
                        "station": st.column_config.TextColumn("Station", width="small"),
                        "watch": st.column_config.TextColumn("Watch", width="small"),
                    },
                )
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
                
                # Professional display formatting: show Relay Team Name explicitly.
                # Time is placed right after the team name for the same mobile
                # reason as the individual table above — four runner-name
                # columns would otherwise push it off-screen entirely.
                df_display = df_filtered_rel.rename(columns={"station": "Relay Team Name"}).copy()
                cols_to_show = ["Relay Team Name", "Time", "division", "runner_1", "runner_2", "runner_3", "runner_4"]
                cols_to_show = [c for c in cols_to_show if c in df_display.columns]

                st.dataframe(
                    df_display[cols_to_show],
                    use_container_width=True,
                    column_config={
                        "Relay Team Name": st.column_config.TextColumn("Team", width="medium"),
                        "Time": st.column_config.TextColumn("Time", width="small"),
                        "division": st.column_config.TextColumn("Div.", width="small"),
                        "runner_1": st.column_config.TextColumn("Runner 1", width="small"),
                        "runner_2": st.column_config.TextColumn("Runner 2", width="small"),
                        "runner_3": st.column_config.TextColumn("Runner 3", width="small"),
                        "runner_4": st.column_config.TextColumn("Runner 4", width="small"),
                    },
                )
            else:
                st.info("No relay times recorded in this filtered category yet.")
        else:
            st.info("No relay times recorded in this category yet.")

with tab_selection:
    st.markdown("### 🎟️ Road to Swansea 2027: Ticket Allocation Algorithm")
    st.write(
        "<b>Selection Criteria:</b> 16 Tickets total, including entry, hotel, breakfast, and transport. "
        "Guaranteed tickets are awarded to the <b>Top 4 Males</b> and <b>Top 4 Females</b>. "
        "The remaining 8 tickets are distributed evenly and proportionately across active age categories "
        "depending on where the top 8 fall.",
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
            st.table(top_m[["name", "age_group", "Time"]])
        else:
            st.warning("Need at least 4 operational male runs to populate.")
            
    with col_sel_f:
        st.markdown("##### 🔴 Guaranteed Female Selection (Top 4)")
        if len(females) >= 4:
            top_f = females.head(4).copy()
            top_f["Time"] = top_f["final_time_sec"].apply(format_time)
            st.table(top_f[["name", "age_group", "Time"]])
        else:
            st.warning("Need at least 4 operational female runs to populate.")
            
    # Proportional Allocation logic
    st.markdown("#### 🎯 Proportional Remaining 8-Ticket Distribution")
    all_ages = ['18-29', '30-34', '35-39', '40-44', '45-49', '50-54', '55+']

    if len(males) >= 4 and len(females) >= 4:
        top_8 = pd.concat([males.head(4), females.head(4)])
        guaranteed_names = set(top_8['name'])
        top_8_ages = list(top_8['age_group'])
        guaranteed_bracket_counts = {age: top_8_ages.count(age) for age in all_ages}

        st.write("<b>Age Brackets represented in Guaranteed Top 8:</b>", unsafe_allow_html=True)
        cols = st.columns(len(all_ages))
        for idx, age in enumerate(all_ages):
            with cols[idx]:
                st.metric(label=f"Bracket {age}", value=guaranteed_bracket_counts[age])

        # Remaining pool = all operational competitors not already guaranteed a seat
        pool = df_all_ind[df_all_ind['category'].isin(['Operational Male', 'Operational Female'])].copy()
        pool = pool[~pool['name'].isin(guaranteed_names)]
        remaining_bracket_counts = {age: int((pool['age_group'] == age).sum()) for age in all_ages}

        # Weight each bracket by its remaining field size, reduced by how many guaranteed
        # seats that bracket already holds (brackets already well-represented in the top 8
        # are "negated" down, not excluded entirely, so one strong bracket can't both sweep
        # the guaranteed 8 AND dominate the proportional 8)
        weights = {age: max(0, remaining_bracket_counts[age] - guaranteed_bracket_counts[age]) for age in all_ages}
        total_weight = sum(weights.values())

        TICKETS_REMAINING = 8

        if total_weight == 0 or pool.empty:
            st.warning("No remaining eligible competitors to distribute the 8 eligible tickets across.")
        else:
            # Largest-remainder apportionment so whole-ticket counts sum exactly to 8
            raw_shares = {age: (weights[age] / total_weight) * TICKETS_REMAINING for age in all_ages}
            quota = {age: int(raw_shares[age]) for age in all_ages}
            shortfall = TICKETS_REMAINING - sum(quota.values())
            remainder_order = sorted(all_ages, key=lambda a: raw_shares[a] - quota[a], reverse=True)
            for age in remainder_order[:shortfall]:
                quota[age] += 1

            # Cap each bracket's quota at how many people are actually available in it,
            # and hand any leftover tickets to the next highest-weighted bracket with spare capacity
            leftover = 0
            for age in all_ages:
                if quota[age] > remaining_bracket_counts[age]:
                    leftover += quota[age] - remaining_bracket_counts[age]
                    quota[age] = remaining_bracket_counts[age]
            while leftover > 0:
                spare = [a for a in all_ages if quota[a] < remaining_bracket_counts[a]]
                if not spare:
                    break
                spare.sort(key=lambda a: weights[a], reverse=True)
                quota[spare[0]] += 1
                leftover -= 1

            st.write("<b>Remaining 8 Tickets — Quota by Bracket:</b>", unsafe_allow_html=True)
            cols2 = st.columns(len(all_ages))
            for idx, age in enumerate(all_ages):
                with cols2[idx]:
                    st.metric(label=f"Bracket {age}", value=quota[age])

            # Within each bracket's quota, take the fastest remaining competitors
            selected_parts = []
            for age in all_ages:
                n = quota[age]
                if n <= 0:
                    continue
                bracket_pool = pool[pool['age_group'] == age].sort_values(by="final_time_sec", ascending=True)
                selected_parts.append(bracket_pool.head(n))

            if selected_parts:
                remaining_selection = pd.concat(selected_parts).sort_values(by="final_time_sec", ascending=True)
                remaining_selection["Time"] = remaining_selection["final_time_sec"].apply(format_time)
                st.write("<b>Remaining 8 Ticket Winners:</b>", unsafe_allow_html=True)
                st.table(remaining_selection[["name", "category", "age_group", "Time"]].reset_index(drop=True))

                st.markdown("---")
                st.write("<b>🏆 Full 16-Ticket Roster (Guaranteed 8 + Proportional 8):</b>", unsafe_allow_html=True)
                full_roster = pd.concat([top_8, remaining_selection]).sort_values(by="final_time_sec", ascending=True).copy()
                full_roster["Time"] = full_roster["final_time_sec"].apply(format_time)
                full_roster.index = range(1, len(full_roster) + 1)
                st.table(full_roster[["name", "category", "age_group", "Time"]])
            else:
                st.warning("No competitors available to fill the remaining 8 tickets with the current data.")

with tab_admin:
    st.markdown("### ⏱️ Marshal Race Time Recording")
    password = st.text_input("Enter Admin Password:", type="password")
    
    admin_pw = st.secrets.get("ADMIN_PASSWORD", "")
    if admin_pw and password == admin_pw:
        st.success("Access Granted. Marshal Timing Form Active.")

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
                mins = st.number_input("Minutes:", min_value=0, max_value=10, value=None, placeholder="0", key="ind_mins")
            with col_s:
                secs = st.number_input("Seconds (and ms):", min_value=0.0, max_value=59.999, value=None, step=0.001, format="%.3f", placeholder="0.000", key="ind_secs")
            
            st.markdown("##### ⚠️ Rule Violations & Penalties")
            penalties = 0
            if st.checkbox("Dropped Cleveland Hose Pack (+10s)", key="ind_p1"): penalties += 10
            if st.checkbox("Improper RTC Tool Table Placement (+5s per tool)", key="ind_p2"): penalties += 5
            if st.checkbox("Improper Forcible Entry Sledge Technique (+10s)", key="ind_p3"): penalties += 10
            if st.checkbox("Missed 50m Hose Drag Marker (+15s)", key="ind_p4"): penalties += 15
            if st.checkbox("Hose Makeup Box Overflow Boundary (+10s)", key="ind_p5"): penalties += 10
            if st.checkbox("Foam Containers Slid or Thrown (+10s)", key="ind_p6"): penalties += 10
            if st.checkbox("Dummy Head / Face Drag Warning (+15s)", key="ind_p7"): penalties += 15
            
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
                mins = st.number_input("Relay Minutes:", min_value=0, max_value=10, value=None, placeholder="0", key="rel_mins")
            with col_s:
                secs = st.number_input("Relay Seconds (and ms):", min_value=0.0, max_value=59.999, value=None, step=0.001, format="%.3f", placeholder="0.000", key="rel_secs")
            
            st.markdown("##### ⚠️ Rule Violations & Penalties")
            penalties = 0
            if st.checkbox("Dropped Cleveland Hose Pack (+10s)", key="rel_p1"): penalties += 10
            if st.checkbox("Improper RTC Tool Table Placement (+5s per tool)", key="rel_p2"): penalties += 5
            if st.checkbox("Improper Forcible Entry Sledge Technique (+10s)", key="rel_p3"): penalties += 10
            if st.checkbox("Missed 50m Hose Drag Marker (+15s)", key="rel_p4"): penalties += 15
            if st.checkbox("Hose Makeup Box Overflow Boundary (+10s)", key="rel_p5"): penalties += 10
            if st.checkbox("Foam Containers Slid or Thrown (+10s)", key="rel_p6"): penalties += 10
            if st.checkbox("Dummy Head / Face Drag Warning (+15s)", key="rel_p7"): penalties += 15
            if st.checkbox("Relay Touch-Tag missed or out of zone (+10s)", key="rel_p8"): penalties += 10
            if st.checkbox("Dummy drag boundary lane crossing (+15s)", key="rel_p9"): penalties += 15
            
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
            "assets/nwfc_course_layout.png.jpg",
            caption="Official Course Layout",
            use_container_width=True,
        )

        st.markdown("---")
        st.markdown("##### 🎥 Full Course Walkthrough")
        # Paste a YouTube, Vimeo, or direct video file URL here once recorded.
        FULL_COURSE_VIDEO_URL = ""
        if FULL_COURSE_VIDEO_URL:
            st.video(FULL_COURSE_VIDEO_URL)
        else:
            st.info("🎥 Full course walkthrough video coming soon.")

    # ---------------- Station-by-Station Guide ----------------
    with info_stations:
        st.markdown("#### Station-by-Station Guide")
        st.caption(
            "⚠️ Editor's note: the apparatus names and penalty codes below are taken directly from the "
            "official course diagram and the Marshal entry forms, so those are accurate. The bracketed "
            "[ ] technique descriptions are placeholders only — replace them with the exact wording from "
            "your rulebook before publishing this page. Claude has not seen the rulebook and has not verified "
            "the actual technique requirements."
        )

        # Paste a video URL per station as footage becomes available.
        STATION_VIDEOS = {
            "hose_drag": "",
            "rtc_tools": "",
            "force_machine": "",
            "dummy_drag": "",
            "hose_lay": "",
            "containers": "",
            "hose_makeup": "",
        }

        stations = [
            {
                "key": "hose_drag",
                "title": "1. Hose Drag",
                "desc": "[Describe the required technique for dragging the coiled hose from the Start marker.]",
                "acceptable": "[State what counts as a clean drag vs a faulted one.]",
                "penalty": "Missed 50m Hose Drag Marker — +15s",
            },
            {
                "key": "rtc_tools",
                "title": "2. RTC Tool Table",
                "desc": "[Describe how tools must be placed on the RTC Tool Table.]",
                "acceptable": "[State correct placement vs incorrect placement.]",
                "penalty": "Improper RTC Tool Table Placement — +5s per tool",
            },
            {
                "key": "force_machine",
                "title": "3. Corhaven Force Machine",
                "desc": "[Describe the required forcible entry sledge technique at the Hammer Placement Mat / Corhaven Force Machine.]",
                "acceptable": "[State correct technique vs incorrect technique.]",
                "penalty": "Improper Forcible Entry Sledge Technique — +10s",
            },
            {
                "key": "dummy_drag",
                "title": "4. Dummy Rescue Drag",
                "desc": "[Describe the required technique for moving the 70kg dummy across the Crash Mat to the Finish.]",
                "acceptable": "[State what triggers a head/face drag warning.]",
                "penalty": "Dummy Head / Face Drag Warning — +15s",
            },
            {
                "key": "hose_lay",
                "title": "5. 70mm Layflat Hose Lay (25m)",
                "desc": "[Describe how the hose must be run out along this 25m section.]",
                "acceptable": "[State correct vs incorrect hose lay technique.]",
                "penalty": "See Hose Makeup Box penalty below for the return leg.",
            },
            {
                "key": "containers",
                "title": "6. 4x Containers Carry (20kg/20L each)",
                "desc": "[Describe how the four containers must be carried from the Container Tray.]",
                "acceptable": "[State that containers must not be thrown, slid, or dropped outside the tray.]",
                "penalty": "Foam Containers Slid or Thrown — +10s",
            },
            {
                "key": "hose_makeup",
                "title": "7. Hose Makeup Box",
                "desc": "[Describe how the hose must be made up (coiled/packed) back into the Lay Flat Hose Box.]",
                "acceptable": "[State the marked boundary the hose must stay within.]",
                "penalty": "Hose Makeup Box Overflow Boundary — +10s",
            },
        ]

        for s in stations:
            with st.expander(s["title"]):
                col_desc, col_video = st.columns([3, 2])
                with col_desc:
                    st.markdown(f"**What to do:** {s['desc']}")
                    st.markdown(f"**Acceptable / Not acceptable:** {s['acceptable']}")
                    st.markdown(f"**Penalty if breached:** {s['penalty']}")
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
        st.write(
            "[Describe the full individual run end-to-end here: the competitor completes every station solo, "
            "start to finish, against the clock. Add any rules specific to the individual event that aren't "
            "already covered in Rules & Penalties.]"
        )
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
             "[Describe exactly what Runner 1 does: the shuttle run and RTC tool stage, and where the handover to Runner 2 happens.]"),
            ("r2", "Runner 2 — Force & Drag",
             "[Describe exactly what Runner 2 does: the force machine and hose drag stage, and where the handover to Runner 3 happens.]"),
            ("r3", "Runner 3 — Makeup & Foam",
             "[Describe exactly what Runner 3 does: the hose makeup and foam container stage, and where the handover to Runner 4 happens.]"),
            ("r4", "Runner 4 — Dummy Rescue",
             "[Describe exactly what Runner 4 does: the dummy rescue drag to the finish.]"),
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
        common_penalties = pd.DataFrame([
            ("Dropped Cleveland Hose Pack", "+10s"),
            ("Improper RTC Tool Table Placement", "+5s per tool"),
            ("Improper Forcible Entry Sledge Technique", "+10s"),
            ("Missed 50m Hose Drag Marker", "+15s"),
            ("Hose Makeup Box Overflow Boundary", "+10s"),
            ("Foam Containers Slid or Thrown", "+10s"),
            ("Dummy Head / Face Drag Warning", "+15s"),
        ], columns=["Violation", "Penalty"])
        st.table(common_penalties)

        st.write("These additional penalties apply to relay entries only:")
        relay_penalties = pd.DataFrame([
            ("Relay Touch-Tag Missed or Out of Zone", "+10s"),
            ("Dummy Drag Boundary Lane Crossing", "+15s"),
        ], columns=["Violation", "Penalty"])
        st.table(relay_penalties)

        st.caption(
            "⚠️ This table is maintained separately from the penalty checkboxes on the Marshal entry forms. "
            "If a penalty or its value changes, update both places so this public page and the live entry "
            "form never drift out of sync."
        )
