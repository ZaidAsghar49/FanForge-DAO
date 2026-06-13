import os
import sqlite3
import re
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Clean Matches Explorer",
    page_icon="🏏",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Paths
CLEAN_DB_PATH = "Dataset/Processed/cricket_clean_38.db"
FULL_DB_PATH = "cricket.db"

# Page Styling for premium dark theme
st.markdown("""
<style>
    .main {
        background-color: #090d16;
        color: #f3f4f6;
    }
    h1, h2, h3 {
        font-family: 'Outfit', sans-serif;
        font-weight: 700;
        letter-spacing: -0.5px;
    }
    .stMetric {
        background-color: #161e31;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid rgba(255,255,255,0.07);
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
    }
    div[data-testid="stMetricValue"] {
        font-size: 2rem;
        font-weight: 700;
    }
    .schema-box {
        background-color: #111827;
        border: 1px solid rgba(255,255,255,0.05);
        padding: 10px;
        border-radius: 6px;
        font-family: monospace;
        font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)

# Helper for database connection
def get_connection(db_path):
    if not os.path.exists(db_path):
        st.error(f"Database file not found at '{db_path}'. Please run extraction script first.")
        st.stop()
    return sqlite3.connect(db_path)

# Cache stats for lightning fast load
@st.cache_data
def get_clean_stats():
    conn = get_connection(CLEAN_DB_PATH)
    stats = {}
    try:
        # Match count
        df_matches = pd.read_sql("SELECT match_format, COUNT(*) as cnt FROM matches GROUP BY match_format", conn)
        stats["format_counts"] = dict(zip(df_matches["match_format"], df_matches["cnt"]))
        stats["total_matches"] = sum(stats["format_counts"].values())

        # Dates
        df_dates = pd.read_sql("SELECT MIN(date) as min_d, MAX(date) as max_d FROM matches", conn)
        stats["min_date"] = df_dates["min_d"].iloc[0] or "N/A"
        stats["max_date"] = df_dates["max_d"].iloc[0] or "N/A"

        # Deliveries
        stats["total_deliveries"] = pd.read_sql("SELECT COUNT(*) as cnt FROM deliveries", conn)["cnt"].iloc[0]

        # Players
        stats["total_players"] = pd.read_sql("SELECT COUNT(DISTINCT player_name) as cnt FROM players", conn)["cnt"].iloc[0]

        # Unique teams
        df_teams = pd.read_sql("SELECT DISTINCT team FROM (SELECT team1 as team FROM matches UNION SELECT team2 as team FROM matches)", conn)
        stats["teams"] = sorted(df_teams["team"].dropna().tolist())

        # Unique players list
        df_players = pd.read_sql("SELECT DISTINCT player_name FROM players ORDER BY player_name", conn)
        stats["players_list"] = df_players["player_name"].dropna().tolist()
        
    except Exception as e:
        st.error(f"Error fetching stats: {e}")
    finally:
        conn.close()
    return stats

# Helper to fetch columns for schema documentation
def get_table_schema(db_path, table_name):
    conn = get_connection(db_path)
    try:
        df_info = pd.read_sql(f"PRAGMA table_info({table_name})", conn)
        return df_info["name"].tolist()
    except Exception:
        return []
    finally:
        conn.close()

# App Title
st.title("🏏 Clean Matches Explorer")
st.markdown("---")

stats = get_clean_stats()

# 4 Dashboard Cards
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Clean Matches", f"{stats.get('total_matches', 0):,}", help="ODI & Test formats only")
with col2:
    st.metric("Total Deliveries", f"{stats.get('total_deliveries', 0):,}")
with col3:
    st.metric("Unique Players", f"{stats.get('total_players', 0):,}")
with col4:
    st.metric("Date Range", f"{stats.get('min_date', '')[:4]} - {stats.get('max_date', '')[:4]}")

tab1, tab2, tab3 = st.tabs(["Browse Matches", "Player Profiles", "SQL Console"])

# -------------------------------------------------------------------------
# TAB 1: BROWSE MATCHES
# -------------------------------------------------------------------------
with tab1:
    st.header("Search & Filter Matches")
    
    # Filter Controls
    c1, c2, c3, c4 = st.columns([1, 1, 2, 3])
    with c1:
        f_format = st.selectbox("Format", ["All", "ODI", "Test"])
    with c2:
        f_year = st.selectbox("Year", ["All"] + [str(y) for y in range(2019, 2027)])
    with c3:
        f_team = st.selectbox("Team Involved", ["All"] + stats.get("teams", []))
    with c4:
        f_search = st.text_input("Search (Venue, City, Award)", placeholder="Search...")

    # Query compilation
    query_parts = ["SELECT * FROM matches WHERE 1=1"]
    params = []

    if f_format != "All":
        query_parts.append("AND match_format = ?")
        params.append(f_format)
    if f_year != "All":
        query_parts.append("AND date LIKE ?")
        params.append(f"{f_year}%")
    if f_team != "All":
        query_parts.append("AND (team1 = ? OR team2 = ?)")
        params.extend([f_team, f_team])
    if f_search:
        query_parts.append("AND (venue LIKE ? OR city LIKE ? OR player_of_match LIKE ?)")
        params.extend([f"%{f_search}%", f"%{f_search}%", f"%{f_search}%"])

    query_parts.append("ORDER BY date DESC")
    full_query = " ".join(query_parts)

    conn = get_connection(CLEAN_DB_PATH)
    df_matches = pd.read_sql(full_query, conn, params=params)
    conn.close()

    st.subheader(f"Found {len(df_matches)} Matches")

    # Render List
    for idx, row in df_matches.iterrows():
        header_text = f"🏏 {row['match_format']} | {row['team1']} vs {row['team2']} ({row['date']}) — {row['city'] or row['venue']}"
        with st.expander(header_text):
            # Display metadata
            st.markdown(f"**Venue:** {row['venue']}, {row['city'] or ''} | **Season:** {row['season']}")
            st.markdown(f"**Toss:** Winner: *{row['toss_winner']}* | Decision: *{row['toss_decision']}*")
            
            result_str = "Draw"
            if row["result_winner"]:
                margin = f" by {int(row['result_margin'])} {row['result_unit']}" if pd.notna(row['result_margin']) else ""
                result_str = f"**{row['result_winner']} won**{margin}"
            st.markdown(f"**Outcome:** {result_str} | **Player of the Match:** {row['player_of_match'] or 'N/A'}")
            
            # Load Scorecards
            st.markdown("### Scorecards")
            mid = row["match_id"]
            
            conn = get_connection(CLEAN_DB_PATH)
            # Fetch Innings Batting
            df_batting = pd.read_sql("""
                SELECT 
                    batting_team, innings, batter, 
                    SUM(runs_batter) as runs, 
                    COUNT(CASE WHEN is_wide = 0 THEN 1 END) as balls, 
                    SUM(CASE WHEN runs_batter = 4 THEN 1 ELSE 0 END) as fours, 
                    SUM(CASE WHEN runs_batter = 6 THEN 1 ELSE 0 END) as sixes, 
                    MAX(wicket_kind) as wicket_kind, 
                    MAX(bowler) as dismisser, 
                    MAX(fielder) as fielder,
                    MAX(player_out) as player_out
                FROM deliveries 
                WHERE match_id = ? 
                GROUP BY batting_team, innings, batter 
                ORDER BY innings, MIN(over * 100 + ball)
            """, conn, params=[mid])

            # Fetch Innings Bowling
            df_bowling = pd.read_sql("""
                SELECT 
                    bowling_team, innings, bowler, 
                    COUNT(CASE WHEN is_wide = 0 AND is_noball = 0 THEN 1 END) as valid_balls, 
                    SUM(runs_batter) + SUM(CASE WHEN is_wide = 1 OR is_noball = 1 THEN runs_extras ELSE 0 END) as runs_conceded, 
                    SUM(CASE WHEN wicket_kind IS NOT NULL AND wicket_kind NOT IN ('run out', 'retired hurt') THEN 1 ELSE 0 END) as wickets,
                    SUM(CASE WHEN runs_total = 0 AND is_wide = 0 AND is_noball = 0 THEN 1 ELSE 0 END) as dot_balls
                FROM deliveries 
                WHERE match_id = ? 
                GROUP BY bowling_team, innings, bowler 
                ORDER BY innings, MIN(over * 100 + ball)
            """, conn, params=[mid])

            # Fetch Innings summaries
            df_summaries = pd.read_sql("""
                SELECT batting_team, innings, SUM(runs_total) as runs, SUM(runs_extras) as extras
                FROM deliveries 
                WHERE match_id = ? 
                GROUP BY batting_team, innings 
                ORDER BY innings
            """, conn, params=[mid])
            
            # Fetch players squad
            df_squads = pd.read_sql("""
                SELECT team, player_name FROM players WHERE match_id = ? ORDER BY team, player_name
            """, conn, params=[mid])
            conn.close()

            # Render Innings Scorecards
            inns_nums = sorted(df_summaries["innings"].tolist())
            if not inns_nums:
                st.info("No delivery logs available for this match.")
            else:
                inns_tabs = st.tabs([f"Innings {i}" for i in inns_nums] + ["Squad Lineups"])
                
                # Render Innings scorecards
                for t_idx, inns_num in enumerate(inns_nums):
                    with inns_tabs[t_idx]:
                        inns_bat = df_batting[df_batting["innings"] == inns_num].copy()
                        inns_bowl = df_bowling[df_bowling["innings"] == inns_num].copy()
                        inns_sum = df_summaries[df_summaries["innings"] == inns_num].iloc[0]

                        st.markdown(f"#### Batting: **{inns_sum['batting_team']}** — **{inns_sum['runs']} Runs** (Extras: {inns_sum['extras']})")
                        
                        # Format batting rows
                        bat_display = []
                        for _, r in inns_bat.iterrows():
                            status = "not out"
                            if r["player_out"] == r["batter"] and pd.notna(r["wicket_kind"]):
                                if r["wicket_kind"] in ["bowled", "lbw"]:
                                    status = f"{r['wicket_kind']} b {r['dismisser']}"
                                elif r["wicket_kind"] == "caught":
                                    status = f"c {r['fielder'] or 'fielder'} b {r['dismisser']}"
                                else:
                                    status = f"{r['wicket_kind']} b {r['dismisser']}"
                            
                            sr = round((r["runs"] / r["balls"]) * 100, 2) if r["balls"] > 0 else 0.0
                            bat_display.append({
                                "Batter": r["batter"],
                                "Dismissal Status": status,
                                "Runs": r["runs"],
                                "Balls": r["balls"],
                                "4s": r["fours"],
                                "6s": r["sixes"],
                                "S/R": sr
                            })
                        st.dataframe(pd.DataFrame(bat_display), width="stretch", hide_index=True)

                        st.markdown("#### Bowling")
                        bowling_display = []
                        for _, r in inns_bowl.iterrows():
                            overs = f"{r['valid_balls'] // 6}.{r['valid_balls'] % 6}"
                            overs_float = float(r['valid_balls']) / 6.0
                            econ = round(r['runs_conceded'] / overs_float, 2) if overs_float > 0 else 0.0
                            bowling_display.append({
                                "Bowler": r["bowler"],
                                "Overs": overs,
                                "Runs Conceded": r["runs_conceded"],
                                "Wickets": r["wickets"],
                                "Dots": r["dot_balls"],
                                "Economy": econ
                            })
                        st.dataframe(pd.DataFrame(bowling_display), width="stretch", hide_index=True)

                # Render Squad tab
                with inns_tabs[-1]:
                    st.markdown("#### Squad Teams Players")
                    teams_grouped = {}
                    for _, r in df_squads.iterrows():
                        teams_grouped.setdefault(r["team"], []).append(r["player_name"])
                    
                    sc1, sc2 = st.columns(2)
                    teams_list = list(teams_grouped.keys())
                    if len(teams_list) >= 1:
                        with sc1:
                            st.markdown(f"**{teams_list[0]}**")
                            st.text("\n".join(teams_grouped[teams_list[0]]))
                    if len(teams_list) >= 2:
                        with sc2:
                            st.markdown(f"**{teams_list[1]}**")
                            st.text("\n".join(teams_grouped[teams_list[1]]))

# -------------------------------------------------------------------------
# TAB 2: PLAYER PROFILES
# -------------------------------------------------------------------------
with tab2:
    st.header("Search Player Profiles")
    selected_player = st.selectbox("Select Player Name", [""] + stats.get("players_list", []))

    if selected_player:
        conn = get_connection(CLEAN_DB_PATH)
        
        # 1. Batting Stats
        df_bat = pd.read_sql("""
            SELECT 
                match_format,
                COUNT(DISTINCT match_id) as innings,
                SUM(runs_batter) as runs,
                COUNT(CASE WHEN is_wide = 0 THEN 1 END) as balls,
                SUM(CASE WHEN runs_batter = 4 THEN 1 ELSE 0 END) as fours,
                SUM(CASE WHEN runs_batter = 6 THEN 1 ELSE 0 END) as sixes
            FROM deliveries 
            WHERE batter = ?
            GROUP BY match_format
        """, conn, params=[selected_player])

        df_bat_match = pd.read_sql("""
            SELECT 
                match_format,
                match_id,
                SUM(runs_batter) as match_runs,
                SUM(CASE WHEN player_out = batter THEN 1 ELSE 0 END) as is_dismissed
            FROM deliveries 
            WHERE batter = ?
            GROUP BY match_format, match_id
        """, conn, params=[selected_player])

        # 2. Bowling Stats
        df_bowl = pd.read_sql("""
            SELECT 
                match_format,
                COUNT(DISTINCT match_id) as matches,
                COUNT(CASE WHEN is_wide = 0 AND is_noball = 0 THEN 1 END) as valid_balls,
                SUM(runs_batter) + SUM(CASE WHEN is_wide = 1 OR is_noball = 1 THEN runs_extras ELSE 0 END) as runs_conceded,
                SUM(CASE WHEN wicket_kind IS NOT NULL AND wicket_kind NOT IN ('run out', 'retired hurt') THEN 1 ELSE 0 END) as wickets
            FROM deliveries
            WHERE bowler = ?
            GROUP BY match_format
        """, conn, params=[selected_player])

        df_bowl_match = pd.read_sql("""
            SELECT 
                match_format,
                match_id,
                SUM(CASE WHEN wicket_kind IS NOT NULL AND wicket_kind NOT IN ('run out', 'retired hurt') THEN 1 ELSE 0 END) as wickets,
                SUM(runs_batter) + SUM(CASE WHEN is_wide = 1 OR is_noball = 1 THEN runs_extras ELSE 0 END) as runs_conceded
            FROM deliveries
            WHERE bowler = ?
            GROUP BY match_format, match_id
        """, conn, params=[selected_player])

        # 3. Runs History (Line chart)
        df_history = pd.read_sql("""
            SELECT 
                d.match_format,
                d.date,
                m.runs
            FROM (
                SELECT match_id, SUM(runs_batter) as runs 
                FROM deliveries 
                WHERE batter = ? 
                GROUP BY match_id
            ) m
            JOIN matches d ON m.match_id = d.match_id
            ORDER BY d.date ASC
        """, conn, params=[selected_player])
        conn.close()

        # Process Batting
        bat_summary = []
        for _, r in df_bat.iterrows():
            fmt = r["match_format"]
            inns_matches = df_bat_match[df_bat_match["match_format"] == fmt]
            dismissals = inns_matches["is_dismissed"].sum()
            high_score = inns_matches["match_runs"].max()
            
            # Check if high score was not out
            is_no = False
            if len(inns_matches) > 0:
                is_no = inns_matches[inns_matches["match_runs"] == high_score]["is_dismissed"].iloc[0] == 0

            hundreds = len(inns_matches[inns_matches["match_runs"] >= 100])
            fifties = len(inns_matches[(inns_matches["match_runs"] >= 50) & (inns_matches["match_runs"] < 100)])
            
            avg = round(r["runs"] / dismissals, 2) if dismissals > 0 else r["runs"]
            sr = round((r["runs"] / r["balls"]) * 100, 2) if r["balls"] > 0 else 0.0
            
            bat_summary.append({
                "Format": fmt,
                "Innings": r["innings"],
                "Runs": r["runs"],
                "Average": avg,
                "Strike Rate": sr,
                "High Score": f"{high_score}*" if is_no else f"{high_score}",
                "100s": hundreds,
                "50s": fifties
            })

        # Process Bowling
        bowl_summary = []
        for _, r in df_bowl.iterrows():
            fmt = r["match_format"]
            inns_bowl = df_bowl_match[df_bowl_match["match_format"] == fmt]
            five_w = len(inns_bowl[inns_bowl["wickets"] >= 5])
            
            # Find best bowling
            best_spell = "N/A"
            if len(inns_bowl) > 0:
                best_w = inns_bowl["wickets"].max()
                best_r = inns_bowl[inns_bowl["wickets"] == best_w]["runs_conceded"].min()
                best_spell = f"{best_w}/{best_r}"

            overs = f"{r['valid_balls'] // 6}.{r['valid_balls'] % 6}"
            overs_float = float(r['valid_balls']) / 6.0
            
            avg = round(r["runs_conceded"] / r["wickets"], 2) if r["wickets"] > 0 else 0.0
            econ = round(r["runs_conceded"] / overs_float, 2) if overs_float > 0 else 0.0
            
            bowl_summary.append({
                "Format": fmt,
                "Matches": r["matches"],
                "Overs": overs,
                "Runs Conceded": r["runs_conceded"],
                "Wickets": r["wickets"],
                "Average": avg,
                "Economy": econ,
                "Best Bowling": best_spell,
                "5W": five_w
            })

        # Render Layout
        st.subheader(f"📊 {selected_player} Career Analytics")
        
        st.markdown("### Batting Profile")
        if bat_summary:
            st.dataframe(pd.DataFrame(bat_summary), width="stretch", hide_index=True)
        else:
            st.info("No batting history available in clean matches.")

        st.markdown("### Bowling Profile")
        if bowl_summary:
            st.dataframe(pd.DataFrame(bowl_summary), width="stretch", hide_index=True)
        else:
            st.info("No bowling history available in clean matches.")

        # Line chart
        if not df_history.empty:
            st.markdown("### Career Runs Progression (Timeline)")
            chart_data = df_history.set_index("date")["runs"]
            st.line_chart(chart_data)

# -------------------------------------------------------------------------
# TAB 3: SQL CONSOLE
# -------------------------------------------------------------------------
with tab3:
    st.header("Interactive SQL Playground")
    st.markdown("Query either the clean subset database or the full 4.18GB database directly.")

    # 1. DB target selector
    db_options = {
        "Clean International Database (cricket_clean_38.db)": CLEAN_DB_PATH,
        "Full Historical Database (cricket.db)": FULL_DB_PATH
    }
    selected_db_label = st.selectbox("Select Target Database", list(db_options.keys()))
    selected_db_path = db_options[selected_db_label]

    # Schema Guide helper
    st.markdown("#### Database Schema Guide")
    with st.expander("Show Available Tables and Column Schemes"):
        if selected_db_path == CLEAN_DB_PATH:
            st.markdown("**1. deliveries Table**")
            st.code(", ".join(get_table_schema(CLEAN_DB_PATH, "deliveries")))
            st.markdown("**2. matches Table**")
            st.code(", ".join(get_table_schema(CLEAN_DB_PATH, "matches")))
            st.markdown("**3. players Table**")
            st.code(", ".join(get_table_schema(CLEAN_DB_PATH, "players")))
        else:
            st.markdown("**1. deliveries Table (Full 37 parameters)**")
            st.code(", ".join(get_table_schema(FULL_DB_PATH, "deliveries")))

    # Preset statements
    presets = {
        "Custom Select Query": "",
        "Top 10 Run Scorers (clean)": "SELECT batter, SUM(runs_batter) as total_runs, COUNT(CASE WHEN is_wide=0 THEN 1 END) as balls FROM deliveries GROUP BY batter ORDER BY total_runs DESC LIMIT 10",
        "Top 10 Wicket Takers (clean)": "SELECT bowler, COUNT(*) as wickets FROM deliveries WHERE wicket_kind IS NOT NULL AND wicket_kind NOT IN ('run out', 'retired hurt') GROUP BY bowler ORDER BY wickets DESC LIMIT 10",
        "Most Frequent Venues": "SELECT venue_name, COUNT(*) as balls_played FROM deliveries GROUP BY venue_name ORDER BY balls_played DESC LIMIT 5" if selected_db_path == FULL_DB_PATH else "SELECT venue, COUNT(*) as matches_played FROM matches GROUP BY venue ORDER BY matches_played DESC LIMIT 5",
        "Home Wins (Full DB)": "SELECT match_id, date, team_a, team_b, home_team, toss_winner FROM deliveries WHERE neutral_venue = 0 LIMIT 10" if selected_db_path == FULL_DB_PATH else "SELECT match_id, date, team1, team2, result_winner FROM matches LIMIT 10"
    }

    # Streamlit reactive state management for preset queries
    if "sql_query" not in st.session_state:
        st.session_state.sql_query = "SELECT * FROM deliveries LIMIT 5"

    selected_preset = st.selectbox("Preset Query Templates", list(presets.keys()))
    
    # Update state if preset is changed
    if presets[selected_preset]:
        st.session_state.sql_query = presets[selected_preset]

    # SQL Input Form
    with st.form("sql_form"):
        sql_input_area = st.text_area("SQL Statement (SELECT Only)", value=st.session_state.sql_query, height=120)
        submit_btn = st.form_submit_button("Execute Query")

    # If preset was selected OR form was submitted, run query
    if submit_btn or presets[selected_preset]:
        sql_to_run = sql_input_area if submit_btn else presets[selected_preset]
        sql_trimmed = sql_to_run.strip()
        
        # Validation checks
        if not re.match(r"^SELECT\b", sql_trimmed, re.IGNORECASE):
            st.error("Forbidden. Only SELECT queries are permitted.")
        else:
            blacklisted = ["insert", "update", "delete", "drop", "alter", "create", "replace", "vacuum", "pragma"]
            is_safe = True
            for kw in blacklisted:
                if re.search(r"\b" + kw + r"\b", sql_trimmed, re.IGNORECASE):
                    st.error(f"Forbidden command keyword detected: '{kw}'")
                    is_safe = False
                    break

            if is_safe:
                with st.spinner("Executing query..."):
                    try:
                        conn = get_connection(selected_db_path)
                        df_res = pd.read_sql(sql_trimmed, conn)
                        conn.close()
                        st.success(f"Execution complete on '{os.path.basename(selected_db_path)}'. Returned {len(df_res)} rows.")
                        st.dataframe(df_res, width="stretch")
                    except Exception as e:
                        st.error(f"SQL Error: {e}")
