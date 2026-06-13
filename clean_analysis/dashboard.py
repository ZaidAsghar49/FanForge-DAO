import streamlit as st
import pandas as pd
import sys
from pathlib import Path
import os
import io
from contextlib import redirect_stdout

# Root Path Addition
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force .env 
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from clean_analysis.validate_model import validate_claim

st.set_page_config(
    page_title="CricketTruth Deterministic Engine",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for a Deterministic, Professional Look
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;600;700&display=swap');
    
    :root {
        --bg-main: #0b0c10;
        --bg-sidebar: #1f2833;
        --accent: #66fcf1;
        --text-primary: #ffffff;
        --text-secondary: #c5c6c7;
        --border-color: #45a29e;
    }

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    .stApp {
        background-color: var(--bg-main);
        color: var(--text-primary);
    }
    
    .main .block-container {
        padding-top: 3rem;
        padding-bottom: 3rem;
        max-width: 1200px;
    }
    
    /* Header Styling */
    h1 {
        font-family: 'JetBrains Mono', monospace;
        font-weight: 700;
        letter-spacing: -0.02em;
        color: var(--accent);
        margin-bottom: 0.5rem;
        border-bottom: 2px solid var(--border-color);
        display: inline-block;
        padding-bottom: 5px;
    }
    
    h5 {
        color: var(--text-secondary);
        font-weight: 400;
        margin-top: 0;
    }
    
    /* Metric Card Styling */
    [data-testid="stMetricValue"] {
        font-family: 'JetBrains Mono', monospace;
        font-weight: 700;
        color: var(--accent) !important;
    }
    
    [data-testid="stMetricLabel"] {
        font-weight: 600;
        color: var(--text-secondary) !important;
        text-transform: uppercase;
        font-size: 0.7rem;
        letter-spacing: 0.1em;
    }

    /* Sidebar Styling */
    section[data-testid="stSidebar"] {
        background-color: var(--bg-sidebar);
        border-right: 1px solid var(--border-color);
    }
    
    /* Button Styling */
    .stButton > button {
        background-color: transparent;
        color: var(--accent);
        border: 1px solid var(--accent);
        border-radius: 4px;
        font-weight: 600;
        font-family: 'JetBrains Mono', monospace;
        padding: 0.5rem 2rem;
        transition: all 0.2s ease;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    
    .stButton > button:hover {
        background-color: var(--accent);
        color: var(--bg-main);
        border: 1px solid var(--accent);
    }

    /* Expander / Card Styling */
    .streamlit-expanderHeader {
        background-color: rgba(69, 162, 158, 0.1);
        border-radius: 4px;
        border: 1px solid rgba(69, 162, 158, 0.3);
    }
</style>
""", unsafe_allow_html=True)

st.title("⚖️ CricketTruth Deterministic Engine")
st.markdown("##### Ground Truth Verification for Cricket Analytics.")

with st.sidebar:
    st.image("https://img.icons8.com/ios-filled/100/66fcf1/scales.png", width=60)
    st.header("Verification Hub")
    st.markdown("""
    - 🔍 **Logic-Based Extraction**
    - ⚖️ **Deterministic Verification**
    - 📑 **Data Provenance**
    - 📈 **Metric Accuracy**
    """)
    st.divider()
    st.header("⚙️ Performance")
    enable_preds = st.checkbox("Enable Predictive Engine", value=False, help="Runs on-the-fly ML training for next-match estimates. Slower.")
    st.caption("v2.4.1-optimized | Engine: DuckDB 0.10.0")


st.info(
    "💡 **Ask a query in plain English.** The engine parses it, resolves the players, "
    "applies hardened filters, and computes precise metrics from the DuckDB dataset."
)

claim_input = st.text_input(
    "Enter a cricket query to analyze:",
    "Jasprit Bumrah economy in ODIs",
)

if st.button("Analyze & Verify"):
    with st.spinner("Parsing query and computing metric..."):
        try:
            # We redirect stdout so it doesn't clutter the terminal while running streamlit
            f = io.StringIO()
            with redirect_stdout(f):
                result = validate_claim(claim_input, skip_predictions=not enable_preds)
            debug_log = f.getvalue()
                
            if result.get("status") == "error":
                st.error(result.get("message", "An error occurred."))
            elif result.get("status") == "no_data":
                st.warning(result.get("message", "Not enough data for the given filters."))
            else:
                if result.get("is_multi_claim"):
                    st.header("Verification Output (Multi-Claim Paragraph)")
                    st.write(f"Decomposed into **{len(result['verdicts'])}** structural claims:")
                    for idx, sub_res in enumerate(result["verdicts"], 1):
                        with st.expander(f"Claim #{idx}: {sub_res.get('subject', 'N/A')} - {sub_res.get('metric', 'N/A')} (Verdict: {sub_res.get('verdict', 'N/A')})", expanded=True):
                            c1, c2, c3, c4 = st.columns(4)
                            c1.metric("Subject", str(sub_res.get("subject", "N/A")))
                            c2.metric("Metric", str(sub_res.get("metric", "N/A")))
                            sub_rv = sub_res.get("real_val")
                            c3.metric("Computed Value", "N/A" if sub_rv is None else f"{sub_rv:.4f}")
                            c4.metric("Data Points", str(sub_res.get("sample_size", "N/A")))

                            st.write(f"**Verdict:** {sub_res.get('verdict', 'N/A')}")
                            if sub_res.get("insight"):
                                st.info(sub_res.get("insight"))

                            sub_filt = sub_res.get("filters") or {}
                            if sub_filt:
                                st.write("**Resolved Filters:**")
                                sf_grid = st.columns(3)
                                for s_idx, (k, v) in enumerate(sub_filt.items()):
                                    display_k = k.replace("_", " ").title()
                                    if k == "over_range":
                                        v = f"Overs {v[0]+1}-{v[1]+1}"
                                    sf_grid[s_idx % 3].markdown(f"**{display_k}**: `{v}`")
                else:
                    st.header("Verification Output")
                    
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Subject", str(result.get("subject", "N/A")))
                    col2.metric("Metric", str(result.get("metric", "N/A")))
                    rv = result.get("real_val")
                    col3.metric("Computed Value", "N/A" if rv is None else f"{rv:.4f}")
                    col4.metric("Data Points", str(result.get("sample_size", "N/A")))

                    verdict = result.get("verdict")
                    if verdict:
                        st.subheader("Final Verdict")
                        st.write(verdict)
                        
                    with st.expander("🔍 Filter Logic & Identity Resolution", expanded=True):
                        f_col1, f_col2 = st.columns([2, 1])
                        with f_col1:
                            st.write("**Resolved Filters:**")
                            filt = result.get("filters") or {}
                            if not filt:
                                st.write("_None_")
                            else:
                                # Display filters in a grid
                                f_grid = st.columns(3)
                                for idx, (k, v) in enumerate(filt.items()):
                                    display_k = k.replace("_", " ").title()
                                    if k == "over_range":
                                        v = f"Overs {v[0]+1}-{v[1]+1}"
                                    f_grid[idx % 3].markdown(f"**{display_k}**: `{v}`")
                        
                        with f_col2:
                            st.write("**Entity Resolution:**")
                            st.write(f"Canonical ID: `{result.get('subject', 'N/A')}`")
                            if "real_meta" in result and "components" in result["real_meta"]:
                                comp = result["real_meta"]["components"]
                                if "innings" in comp:
                                    st.write(f"Innings Index: `{comp['innings']}`")
                        
                    if "real_meta" in result and result["real_meta"]:
                        meta = result["real_meta"]
                        st.markdown("#### Statistical Context")
                        meta_col1, meta_col2, meta_col3 = st.columns(3)
                        meta_col1.caption(f"**Engine Formula:** {meta.get('formula', 'N/A')}")
                        if "dismissals" in meta:
                            meta_col2.caption(
                                f"**Sample Balls:** {meta.get('balls', 0):,}  |  **Dismissals:** {meta.get('dismissals', 0)}"
                            )
                        else:
                            meta_col2.caption(
                                f"**Sample Overs:** {meta.get('overs', 0):.1f}  |  **Wickets:** {meta.get('wickets', 0)}"
                            )
                        meta_col3.caption(f"**Innings Coverage:** {meta.get('innings', 0)} matches")

                st.divider()

                with st.expander("Debug log (engine output)"):
                    st.text(debug_log if debug_log else "(empty)")
                        
        except Exception as e:
            st.error(f"Pipeline Execution Failed: {e}")
