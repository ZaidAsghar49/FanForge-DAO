import streamlit as st
import pandas as pd
import sys
from pathlib import Path
import os
import io
from contextlib import redirect_stdout

# Root Path Addition
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force .env 
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from scripts.analysis.validate_model import validate_claim

st.set_page_config(
    page_title="CricketTruth AI",
    page_icon="🏏",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for Premium Look
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    .stApp {
        background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
        color: #e6edf3;
    }
    
    .main .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    
    /* Title Styling */
    h1 {
        font-weight: 800;
        letter-spacing: -0.05em;
        background: linear-gradient(90deg, #58a6ff 0%, #bc8cff 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 1.5rem;
    }
    
    /* Metric Card Styling */
    [data-testid="stMetricValue"] {
        font-weight: 700;
        color: #58a6ff !important;
    }
    
    [data-testid="stMetricLabel"] {
        font-weight: 600;
        color: #8b949e !important;
        text-transform: uppercase;
        font-size: 0.75rem;
        letter-spacing: 0.05em;
    }

    /* Sidebar Styling */
    section[data-testid="stSidebar"] {
        background-color: rgba(22, 27, 34, 0.8);
        backdrop-filter: blur(10px);
        border-right: 1px solid rgba(255, 255, 255, 0.1);
    }
    
    /* Button Styling */
    .stButton > button {
        background: linear-gradient(90deg, #238636 0%, #2ea043 100%);
        color: white;
        border: none;
        border-radius: 8px;
        font-weight: 600;
        padding: 0.5rem 2rem;
        transition: all 0.3s ease;
        box-shadow: 0 4px 12px rgba(35, 134, 54, 0.3);
    }
    
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 16px rgba(35, 134, 54, 0.4);
        background: linear-gradient(90deg, #2ea043 0%, #3fb950 100%);
    }

    /* Expander / Card Styling */
    .streamlit-expanderHeader {
        background-color: rgba(48, 54, 61, 0.4);
        border-radius: 8px;
        border: 1px solid rgba(255, 255, 255, 0.1);
    }
</style>
""", unsafe_allow_html=True)

st.title("🏏 CricketTruth AI Intelligence Platform")
st.markdown("##### The world's first unified engine for Claim Verification and AI Insights.")

with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/000000/cricket.png", width=80)
    st.header("Intelligence Hub")
    st.markdown("""
    - 🗣️ **Natural Language Analytics**
    - ✅ **Claim Verification**
    - 🔮 **Predictive Engine**
    - 📊 **Visual Insights**
    """)
    st.divider()
    st.caption("v2.4.0-hardened | Engine: DuckDB 0.10.0")


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
                result = validate_claim(claim_input)
            debug_log = f.getvalue()
                
            if result.get("status") == "error":
                st.error(result.get("message", "An error occurred."))
            elif result.get("status") == "no_data":
                st.warning(result.get("message", "Not enough data for the given filters."))
            else:
                st.header("Result")
                
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Subject", str(result.get("subject", "N/A")))
                col2.metric("Metric", str(result.get("metric", "N/A")))
                rv = result.get("real_val")
                col3.metric("Value", "N/A" if rv is None else f"{rv:.4f}")
                col4.metric("Sample size", str(result.get("sample_size", "N/A")))

                verdict = result.get("verdict")
                if verdict:
                    st.subheader("Verdict")
                    st.write(verdict)
                    
                with st.expander("Filters applied"):
                    st.json(result.get("filters") or {})
                    
                if "real_meta" in result and result["real_meta"]:
                    meta = result["real_meta"]
                    st.markdown("#### Explainability")
                    meta_col1, meta_col2, meta_col3 = st.columns(3)
                    meta_col1.caption(f"**Formula Used:** {meta.get('formula', 'N/A')}")
                    if "dismissals" in meta:
                        meta_col2.caption(
                            f"**Balls Faced:** {meta.get('balls', 0):,}  |  **Dismissals:** {meta.get('dismissals', 0)}"
                        )
                    else:
                        meta_col2.caption(
                            f"**Overs Bowled:** {meta.get('overs', 0):.1f}  |  **Wickets:** {meta.get('wickets', 0)}"
                        )
                    meta_col3.caption(f"**Sample Size:** {meta.get('innings', 0)} Innings")

                st.divider()

                with st.expander("Debug log (engine output)"):
                    st.text(debug_log if debug_log else "(empty)")
                        
        except Exception as e:
            st.error(f"Pipeline Execution Failed: {e}")
