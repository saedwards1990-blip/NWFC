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

st.set_page_collab_width = True
st.set_page_config(
    page_title="North Wales Firefighter Challenge (NWFC)",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==========================================
# CLOUD BACKEND CONFIGURATION (GOOGLE SHEETS)
# ==========================================
# To enable permanent, zero-stress cloud storage across weeks, populate these URLs.
# If left blank, the app will automatically and gracefully fall back to local SQLite storage.
GSHEET_INDIVIDUALS_CSV = st.sidebar.text_input(
    "Google Sheet Individuals CSV URL:",
    value=st.secrets.get("GSHEET_INDIVIDUALS_CSV", ""),
    help="Paste the web-published CSV link or export link of your Google Sheet for individuals."
)

GSHEET_RELAYS_CSV = st.sidebar.text_input(
    "Google Sheet Relays CSV URL:",
    value=st.secrets.get("GSHEET_RELAYS_CSV", ""),
    help="Paste the web-published CSV link or export link of your Google Sheet for relays."
)

APPS_SCRIPT_URL = st.sidebar.text_input(
    "Google Apps Script Web App URL:",
    value=st.secrets.get("APPS_SCRIPT_URL", ""),
    type="password",
    help="Paste the deployed Google Apps Script Web App URL to enable writing directly to your Google Sheet."
)

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
            ("Mark Owen", "HQ", "Finance", "Support Staff", "40-44", 102.5, 5, 107.5),
            ("Sian Parry", "St Asaph", "Control", "Support Staff", "18-29", 98.0, 0, 98.0),
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
    st.success(f"Logged {name}'s run locally: {formatted_t}")
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
    st.success(f"Logged {team_name} Relay run locally: {formatted_t}")
    return True


st.title("🏆 NORTH WALES FIREFIGHTER CHALLENGE (NWFC)")
st.subheader("Official Live Leaderboard & Ticket Selection System")

# Tab Layout
tab_leaderboard, tab_selection, tab_admin, tab_course = st.tabs([
    "📊 Live Standings", 
    "🎟️ Swansea 2027 Ticket Selection", 
    "⏱️ Marshal Timer & Admin", 
    "🗺️ Course Diagram"
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
                
                # Dynamic visual columns based on selection
                display_cols = ["name", "station", "watch", "age_group", "Time"]
                if "category" in df_filtered.columns and category_filter == "All Operational Staff":
                    display_cols.insert(3, "category")
                st.dataframe(df_filtered[display_cols], use_container_width=True)
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
                
                # Professional display formatting: show Relay Team Name explicitly
                df_display = df_filtered_rel.rename(columns={"station": "Relay Team Name"}).copy()
                cols_to_show = ["Relay Team Name", "division", "runner_1", "runner_2", "runner_3", "runner_4", "Time"]
                cols_to_show = [c for c in cols_to_show if c in df_display.columns]
                
                st.dataframe(df_display[cols_to_show], use_container_width=True)
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
        top_8_ages = list(males.head(4)['age_group']) + list(females.head(4)['age_group'])
        age_counts = {age: top_8_ages.count(age) for age in all_ages}
        
        st.write("<b>Age Brackets represented in Top 8:</b>", unsafe_allow_html=True)
        
        # Display as columns
        cols = st.columns(len(all_ages))
        for idx, age in enumerate(all_ages):
            with cols[idx]:
                st.metric(label=f"Bracket {age}", value=age_counts[age])
                
        # Proportional remainder math
        st.info("The remaining 8 tickets are automatically distributed based on the proportion of active registrants in each of the 7 brackets.")

with tab_admin:
    st.markdown("### ⏱️ Marshal Race Time Recording")
    password = st.text_input("Enter Admin Password:", type="password")
    
    if password == "nwfc2026":
        st.success("Access Granted. Marshal Timing Form Active.")
        
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
            with st.form("ind_form"):
                name = st.text_input("Competitor Name:")
                station = st.selectbox("Station:", STATIONS_LIST)
                watch = st.selectbox("Watch / Dept / Sector:", WATCHES_LIST)
                category = st.selectbox("Class Category:", ["Operational Male", "Operational Female", "Non-Operational"])
                age_group = st.selectbox("Age Bracket:", ['18-29', '30-34', '35-39', '40-44', '45-49', '50-54', '55+'])
                
                st.markdown("##### ⏱️ Raw Stopwatch Time")
                mins = st.number_input("Minutes:", min_value=0, max_value=10, value=1)
                secs = st.number_input("Seconds (and ms):", min_value=0.0, max_value=59.999, value=30.0, step=0.001, format="%.3f")
                
                st.markdown("##### ⚠️ Rule Violations & Penalties")
                penalties = 0
                if st.checkbox("Dropped Cleveland Hose Pack (+10s)"): penalties += 10
                if st.checkbox("Improper RTC Tool Table Placement (+5s per tool)"): penalties += 5
                if st.checkbox("Improper Forcible Entry Sledge Technique (+10s)"): penalties += 10
                if st.checkbox("Missed 50m Hose Drag Marker (+10s)"): penalties += 10
                if st.checkbox("Hose Makeup Box Overflow Boundary (+10s)"): penalties += 10
                if st.checkbox("Foam Containers Thrown/Fallen/Not Within Tray (+10s)"): penalties += 10
                if st.checkbox("Dummy Head/Face Drag Warning / Lifted Off Ground / NOt Lifted & Dragged (+15s)"): penalties += 15
                
                submit = st.form_submit_button("Log Run and Sync Leaderboard")
                
                if submit:
                    if not name.strip():
                        st.error("⚠️ Submission Blocked: Competitor Name is required. Please fill in the competitor's name to complete the entry.")
                    elif mins == 0 and secs == 0.0:
                        st.error("⚠️ Submission Blocked: Raw Stopwatch Time cannot be 00:00.000. Please enter the raw run time.")
                    else:
                        raw_tot = mins * 60 + secs
                        final_tot = raw_tot + penalties
                        write_individual_run(name, station, watch, category, age_group, raw_tot, penalties, final_tot)
                    
        else:
            st.markdown("#### 👥 Relay Team Entry Form")
            with st.form("relay_form"):
                relay_team_name = st.text_input("Relay Team Name:")
                division = st.selectbox("Relay Division:", ["Male", "Female", "Mixed"])
                
                st.markdown("##### 🏃 Running Order (4-Person Team)")
                r1 = st.text_input("Runner 1 (Shuttle Run & RTC):")
                r2 = st.text_input("Runner 2 (Force Machine & Hose Drag):")
                r3 = st.text_input("Runner 3 (Hose Makeup & Foam Containers):")
                r4 = st.text_input("Runner 4 (Dummy Drag):")
                
                st.markdown("##### ⏱️ Raw Relay Stopwatch Time")
                mins = st.number_input("Relay Minutes:", min_value=1, max_value=10, value=3)
                secs = st.number_input("Relay Seconds (and ms):", min_value=0.0, max_value=59.999, value=15.0, step=0.001, format="%.3f")
                
                st.markdown("##### ⚠️ Rule Violations & Penalties")
                penalties = 0
                if st.checkbox("Dropped Cleveland Hose Pack (+10s)", key="rel_p1"): penalties += 10
                if st.checkbox("Improper RTC Tool Table Placement (+5s per tool)", key="rel_p2"): penalties += 5
                if st.checkbox("Improper Forcible Entry Sledge Technique (+10s)", key="rel_p3"): penalties += 10
                if st.checkbox("Missed 50m Hose Drag Marker (+10s)", key="rel_p4"): penalties += 10
                if st.checkbox("Hose Makeup Box Overflow Boundary (+10s)", key="rel_p5"): penalties += 10
                if st.checkbox("Foam Containers Thrown/Fallen/Not Within Tray (+10s)", key="rel_p6"): penalties += 10
                if st.checkbox("Dummy Head/Face Drag Warning / Lifted Off Ground / NOt Lifted & Dragged (+15s)", key="rel_p7"): penalties += 15
                if st.checkbox("Relay Touch-Tag missed or out of zone (+10s)", key="rel_p8"): penalties += 10
                if st.checkbox("Dummy drag boundary lane crossing (+15s)", key="rel_p9"): penalties += 15
                
                submit = st.form_submit_button("Log Relay Team and Sync Leaderboard")
                
                if submit:
                    # Strict validation block to prevent "Enter-to-submit" empty entries
                    missing_fields = []
                    if not relay_team_name.strip():
                        missing_fields.append("Relay Team Name")
                    if not r1.strip():
                        missing_fields.append("Runner 1 (Shuttle Run & RTC)")
                    if not r2.strip():
                        missing_fields.append("Runner 2 (Force Entry & Hose Drag)")
                    if not r3.strip():
                        missing_fields.append("Runner 3 (Hose Makeup & Foam Containers)")
                    if not r4.strip():
                        missing_fields.append("Runner 4 (Dummy Drag)")
                    
                    if missing_fields:
                        st.error(f"⚠️ Submission Blocked: Incomplete Entry! Please fill in all required fields: {', '.join(missing_fields)}.")
                    elif mins == 0 and secs == 0.0:
                        st.error("⚠️ Submission Blocked: Raw Relay Stopwatch Time cannot be 00:00.000. Please enter the raw run time.")
                    else:
                        raw_tot = mins * 60 + secs
                        final_tot = raw_tot + penalties
                        write_relay_run(relay_team_name, division, r1, r2, r3, r4, raw_tot, penalties, final_tot)

    else:
        st.info("Enter password '*******' in the field above to activate the marshal logger panel.")
        
        # Guide Panel for Google Sheets Setup
        st.markdown("---")
        st.markdown("### 📂 How to Configure Google Sheets Cloud Backend")
        st.write(
            "To enable zero-stress cloud storage so you do not lose data over the month-long tournament, follow these simple steps:"
        )
        st.markdown("""
        1. **Create and Share your Google Sheet (No Technical "Publish to Web" Needed!):**
           * Create a standard Google Sheet with two tabs: `individuals` and `relays`.
           * Create the header row in `individuals`: `id`, `name`, `station`, `watch`, `category`, `age_group`, `raw_time_sec`, `penalties_sec`, `final_time_sec`, `formatted_time`.
           * Create the header row in `relays`: `id`, `station`, `watch`, `division`, `runner_1`, `runner_2`, `runner_3`, `runner_4`, `raw_time_sec`, `penalties_sec`, `final_time_sec`, `formatted_time`.
           * Click the blue **Share** button in the top-right corner of Google Sheets. Under **General access**, change it from "Restricted" to **Anyone with the link can view** (this allows the app to read your live data).
           * Simply copy the **standard URL from your Chrome address bar** for each tab! 
             * For the `individuals` tab, copy the link and paste it directly into **Google Sheet Individuals CSV URL** in the sidebar.
             * For the `relays` tab, copy the link and paste it directly into **Google Sheet Relays CSV URL** in the sidebar.
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
           * Copy the generated **Web App URL** and paste it into the **Google Apps Script Web App URL** field in the sidebar.
        3. **Enjoy Zero Data Loss:** 
           * Once these are set, the app will securely read and write directly to your cloud sheet. Closing the app, browser, or signing out will never wipe your data!
        """)

with tab_course:
    st.markdown("### 🗺️ Official Top-Down Course Layout Schema (v15.0)")
    st.write(
        "The official, single-lane, vertical track layout. Designed with compact station-yard boundaries.",
        unsafe_allow_html=True
    )
    st.image(
    "assets/nwfc_course_layout.png.jpg",
    caption="Official Course Layout",
    use_container_width=True,
)
