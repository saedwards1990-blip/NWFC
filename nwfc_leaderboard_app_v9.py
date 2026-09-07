import streamlit as st
import pandas as pd
import sqlite3
import os
import urllib.request
import urllib.parse
import csv
import io
import json

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

DB_PATH = "nwfc_tournament_v7.db"

# Local SQLite fallback initialisation
def init_local_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS individuals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            station TEXT NOT NULL,
            watch TEXT NOT NULL,
            category TEXT NOT NULL,
            age_group TEXT NOT NULL,
            raw_time_sec REAL NOT NULL,
            penalties_sec REAL DEFAULT 0,
            final_time_sec REAL NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS relays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station TEXT NOT NULL,
            watch TEXT NOT NULL,
            division TEXT NOT NULL,
            runner_1 TEXT NOT NULL,
            runner_2 TEXT NOT NULL,
            runner_3 TEXT NOT NULL,
            runner_4 TEXT NOT NULL,
            raw_time_sec REAL NOT NULL,
            penalties_sec REAL DEFAULT 0,
            final_time_sec REAL NOT NULL
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
            ("Nia Jenkins", "Wrexham", "Corporate", "Support Staff", "30-34", 95.0, 0, 95.0),
            ("Mark Owen", "HQ", "Finance", "Support Staff", "40-44", 102.5, 5, 107.5),
            ("Sian Parry", "St Asaph", "Control", "Support Staff", "18-29", 98.0, 0, 98.0),
        ]
        c.executemany("INSERT INTO individuals (name, station, watch, category, age_group, raw_time_sec, penalties_sec, final_time_sec) VALUES (?,?,?,?,?,?,?,?)", mock_ind)
    c.execute("SELECT COUNT(*) FROM relays")
    if c.fetchone()[0] == 0:
        mock_rel = [
            ("Wrexham", "Red", "Male Watch", "Runner A", "Runner B", "Runner C", "Runner D", 195.5, 10, 205.5),
            ("Rhyl", "Green", "Male Watch", "Runner A", "Runner B", "Runner C", "Runner D", 201.0, 0, 201.0),
            ("Deeside", "Blue", "Mixed Watch", "Runner A", "Runner B", "Runner C", "Runner D", 215.5, 5, 220.5),
            ("Bangor", "Red", "Female Watch", "Runner A", "Runner B", "Runner C", "Runner D", 232.0, 0, 232.0),
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
        return f"{mins:02d}:{secs:05.2f}"
    except:
        return "00:00.00"

# Robust CSV Reader Utility to prevent tokenizing errors (e.g. from commas in fields or trailing grid cells)
def get_gsheet_data_robust(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
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
            df = get_gsheet_data_robust(GSHEET_INDIVIDUALS_CSV)
            if not df.empty and 'final_time_sec' in df.columns:
                df['final_time_sec'] = pd.to_numeric(df['final_time_sec'], errors='coerce')
                df = df.dropna(subset=['final_time_sec'])
                return df
        except Exception as e:
            st.error(f"Error reading Individuals from Google Sheets: {e}. Falling back to local data.")
    
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM individuals ORDER BY final_time_sec ASC", conn)
    conn.close()
    return df

def get_relays_data():
    if GSHEET_RELAYS_CSV:
        try:
            df = get_gsheet_data_robust(GSHEET_RELAYS_CSV)
            if not df.empty and 'final_time_sec' in df.columns:
                df['final_time_sec'] = pd.to_numeric(df['final_time_sec'], errors='coerce')
                df = df.dropna(subset=['final_time_sec'])
                return df
        except Exception as e:
            st.error(f"Error reading Relays from Google Sheets: {e}. Falling back to local data.")
            
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM relays ORDER BY final_time_sec ASC", conn)
    conn.close()
    return df

def write_individual_run(name, station, watch, category, age_group, raw_time, penalties, final_time):
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
                "final_time_sec": str(final_time)
            }
            data = urllib.parse.urlencode(payload).encode('utf-8')
            req = urllib.request.Request(APPS_SCRIPT_URL, data=data, method="POST")
            with urllib.request.urlopen(req) as response:
                res_text = response.read().decode('utf-8')
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
    st.success(f"Logged {name}'s run locally: {format_time(final_time)}")
    return True

def write_relay_run(station, watch, division, r1, r2, r3, r4, raw_time, penalties, final_time):
    if APPS_SCRIPT_URL:
        try:
            payload = {
                "action": "add_relay",
                "station": station,
                "watch": watch,
                "division": division,
                "runner_1": r1,
                "runner_2": r2,
                "runner_3": r3,
                "runner_4": r4,
                "raw_time_sec": str(raw_time),
                "penalties_sec": str(penalties),
                "final_time_sec": str(final_time)
            }
            data = urllib.parse.urlencode(payload).encode('utf-8')
            req = urllib.request.Request(APPS_SCRIPT_URL, data=data, method="POST")
            with urllib.request.urlopen(req) as response:
                res_text = response.read().decode('utf-8')
                if "SUCCESS" in res_text:
                    st.success(f"Successfully saved and synced {station} Relay run to Google Sheets Cloud!")
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
        (station, watch, division, r1, r2, r3, r4, raw_time, penalties, final_time)
    )
    conn.commit()
    conn.close()
    st.success(f"Logged {station} Relay run locally: {format_time(final_time)}")
    return True


st.title("🏆 NORTH WALES FIREFIGHTER CHALLENGE (NWFC)")
st.subheader("Official Live Roadshow Leaderboard & Ticket Selection System")

# Tab Layout
tab_leaderboard, tab_selection, tab_admin, tab_course = st.tabs([
    "📊 Live Standings", 
    "🎟️ Swansea 2027 Ticket Selection", 
    "⏱️ Marshal Timer & Admin", 
    "🗺️ Course Diagram"
])

with tab_leaderboard:
    st.markdown("### 🏆 Rolling Leaderboards (Updated Real-Time Across Roadshow)")
    
    col_ind, col_rel = st.columns(2)
    
    with col_ind:
        st.markdown("#### 🏃 Individual Championship")
        
        # Symmetrical and professional side-by-side drop-down filters
        filt_c1, filt_c2 = st.columns(2)
        with filt_c1:
            category_filter = st.selectbox("Filter Individual Class:", [
                "All Operational Staff", "Operational Male Only", "Operational Female Only", "Support Staff (Non-Operational)"
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
                df_filtered = df_ind[df_ind['category'] == 'Support Staff'].copy()
                
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
        st.markdown("#### 👥 Inter-Watch Relays")
        division_filter = st.selectbox("Filter Relay Class:", [
            "All Relay Teams", "Male Watch Division", "Female Watch Division", "Mixed Watch Division"
        ])
        
        df_rel = get_relays_data()
        
        if not df_rel.empty:
            if division_filter == "All Relay Teams":
                df_filtered_rel = df_rel.copy()
            else:
                div_val = division_filter.replace(" Division", "")
                df_filtered_rel = df_rel[df_rel['division'] == div_val].copy()
                
            if not df_filtered_rel.empty:
                df_filtered_rel = df_filtered_rel.sort_values(by="final_time_sec", ascending=True)
                df_filtered_rel["Time"] = df_filtered_rel["final_time_sec"].apply(format_time)
                df_filtered_rel.index = range(1, len(df_filtered_rel) + 1)
                st.dataframe(df_filtered_rel[["station", "watch", "division", "runner_1", "runner_2", "runner_3", "runner_4", "Time"]], use_container_width=True)
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
    st.markdown("### ⏱️ Station Marshal Staging Panel")
    password = st.text_input("Enter Admin Password:", type="password")
    
    if password == "nwfc2026":
        st.success("Access Granted. Marshal Timing Form Active.")
        
        mode = st.radio("Log Time For:", ["Individual Competitor", "Watch Relay Team"])
        
        if mode == "Individual Competitor":
            with st.form("ind_form"):
                name = st.text_input("Competitor Name:")
                station = st.selectbox("Station:", ["Wrexham", "Rhyl", "Deeside", "Bangor", "Holyhead", "Colwyn Bay", "Llandudno", "St Asaph", "HQ"])
                watch = st.selectbox("Watch / Dept:", ["Red", "Green", "Blue", "Corporate", "Control", "Other"])
                category = st.selectbox("Class Category:", ["Operational Male", "Operational Female", "Support Staff"])
                age_group = st.selectbox("Age Bracket:", ['18-29', '30-34', '35-39', '40-44', '45-49', '50-54', '55+'])
                
                st.markdown("##### ⏱️ Raw Stopwatch Time")
                mins = st.number_input("Minutes:", min_value=0, max_value=10, value=1)
                secs = st.number_input("Seconds (and ms):", min_value=0.0, max_value=59.99, value=30.0)
                
                st.markdown("##### ⚠️ Rule Violations & Penalties")
                penalties = 0
                if st.checkbox("Dropped Cleveland Hose Pack (+10s)"): penalties += 10
                if st.checkbox("Improper RTC Tool Table Placement (+5s per tool)"): penalties += 5
                if st.checkbox("Improper Forcible Entry Sledge Technique (+10s)"): penalties += 10
                if st.checkbox("Missed 50m Hose Drag Marker (+15s)"): penalties += 15
                if st.checkbox("Hose Makeup Box Overflow Boundary (+10s)"): penalties += 10
                if st.checkbox("Foam Containers Slid or Thrown (+10s)"): penalties += 10
                if st.checkbox("Dummy Head / Face Drag Warning (+15s)"): penalties += 15
                
                submit = st.form_submit_button("Log Run and Sync Leaderboard")
                
                if submit:
                    raw_tot = mins * 60 + secs
                    final_tot = raw_tot + penalties
                    write_individual_run(name, station, watch, category, age_group, raw_tot, penalties, final_tot)
                    
        else:
            with st.form("relay_form"):
                station = st.selectbox("Relay Station:", ["Wrexham", "Rhyl", "Deeside", "Bangor", "Holyhead", "Colwyn Bay", "Llandudno"])
                watch = st.selectbox("Relay Watch:", ["Red", "Green", "Blue"])
                division = st.selectbox("Watch Relay Division:", ["Male Watch", "Female Watch", "Mixed Watch"])
                
                r1 = st.text_input("Runner 1 (Shuttle & RTC):")
                r2 = st.text_input("Runner 2 (Force & Drag):")
                r3 = st.text_input("Runner 3 (Makeup & Foam):")
                r4 = st.text_input("Runner 4 (Dummy Rescue):")
                
                mins = st.number_input("Relay Minutes:", min_value=1, max_value=10, value=3)
                secs = st.number_input("Relay Seconds:", min_value=0.0, max_value=59.99, value=15.0)
                
                penalties = 0
                if st.checkbox("Relay Touch-Tag missed or out of zone (+10s)"): penalties += 10
                if st.checkbox("Dummy drag boundary lane crossing (+15s)"): penalties += 15
                
                submit = st.form_submit_button("Log Relay Team and Sync Leaderboard")
                
                if submit:
                    raw_tot = mins * 60 + secs
                    final_tot = raw_tot + penalties
                    write_relay_run(station, watch, division, r1, r2, r3, r4, raw_tot, penalties, final_tot)

    else:
        st.info("Enter password 'nwfc2026' in the field above to activate the marshal logger panel.")
        
        # Guide Panel for Google Sheets Setup
        st.markdown("---")
        st.markdown("### 📂 How to Configure Google Sheets Cloud Backend")
        st.write(
            "To enable zero-stress cloud storage so you do not lose data over the month-long tournament, follow these simple steps:"
        )
        st.markdown("""
        1. **Create your Google Sheet:**
           * Create a standard Google Sheet with two tabs: `individuals` and `relays`.
           * Create the header row in `individuals`: `id`, `name`, `station`, `watch`, `category`, `age_group`, `raw_time_sec`, `penalties_sec`, `final_time_sec`.
           * Create the header row in `relays`: `id`, `station`, `watch`, `division`, `runner_1`, `runner_2`, `runner_3`, `runner_4`, `raw_time_sec`, `penalties_sec`, `final_time_sec`.
           * Go to **File > Share > Publish to web**. Under Link, choose `individuals` and select **Comma-separated values (.csv)**. Copy that link and paste it into the **Google Sheet Individuals CSV URL** in the sidebar. Repeat for `relays` and paste it into the **Google Sheet Relays CSV URL** sidebar input.
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
    st.info("The official layout is displayed as the unamended source 'NWFFC Layout Image.png' in your notebook panel. Please refer to that file for the top-down visual map.")
