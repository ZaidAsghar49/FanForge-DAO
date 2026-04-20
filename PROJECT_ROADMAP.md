# CricketTruth AI: Project Roadmap & Technical Vision

## 1. Executive Summary
**CricketTruth AI** is an advanced fact-checking engine designed to verify natural language claims about cricket statistics. By combining Large Language Models (LLMs) with a rigorous historical database, it bridges the gap between casual discussion ("Kohli is a beast in Australia") and hard data ("Kohli averages 54.08 in Australia").

## 2. Core Architecture

The system operates on a **Three-Layer Architecture**:

### Layer 1: The Interpretation Layer (AI Powered)
*   **Input**: Natural Language String (e.g., "Babar Azam struggles against spin").
*   **Engine**: Cohere / Gemini API.
*   **Output**: Semantic JSON Query.
    *   *Subject*: "Babar Azam" (Mapped to `Babar Azam` ID)
    *   *Stat*: "Average"
    *   *Filters*: `{"opponent_type": "Spin"}`
*   **Role**: Decouples "Human Speak" from "Database Logic".

### Layer 2: The Logic Layer (The Validation Engine)
*   **Dynamic Role Context**: Unlike simple stats engines, this layer understands context.
    *   If the user asks for "Runs", the subject is treated as a **Batter**, even if they are a bowler (e.g., Rashid Khan batting stats).
    *   If the user asks for "Wickets", the subject is treated as a **Bowler**.
*   **Complex Filtering**:
    *   **Versus Context**: Batter vs Pace/Spin, Batter vs Specific Team, Bowler vs Specific Batter.
    *   **Venue Normalization**: "In matches played in England" (handling City-to-Country mapping).

### Layer 3: The Data Layer (Knowledge Base)
*   **Matches DB**: Ball-by-ball granularity (`matches.csv`). This allows for infinite query flexibility (e.g., "Strike rate in the last 5 overs").
*   **Bowler Identity DB**: A specialized lookup (`bowlers.csv`) classifying every player as 'Spin' or 'Pace', enabling high-level tactical analysis.

---

## 3. Current Capabilities (active)
*   **Auto-Parsing**: Successfully converts "Glenn Maxwell average in Australia" -> SQL-like query.
*   **Fuzzy Name Matching**: Handles typos and aliases (`Maxwell` -> `GJ Maxwell`).
*   **Location Awareness**: Understands that "Melbourne" implies "Australia".
*   **Basic Validation**: Calculates `Average`, `Runs`, and `Wickets` and compares them against user claims to output a `% Accuracy`.

## 4. The Revision: Next Steps & Fixes

### A. Intelligent Role Switching (Urgent)
*   **Problem**: Currently, the system guesses if a player is a Batter or Bowler based on total volume.
*   **Fix**: The `Stat Type` must dictate the role.
    *   Query: "Shahid Afridi Runs" -> Force **Batter** Mode.
    *   Query: "Shahid Afridi Wickets" -> Force **Bowler** Mode.

### B. "Versus" Logic Refinement
*   **Problem**: "Runs against Pace" currently fails for All-Rounders if they are classified as Bowlers default.
*   **Fix**: Ensure the `Join` with `bowlers.csv` happens on the *Opponent's* name, not the Subject's name, when in Batting Mode.

### C. Performance Optimization
*   **Problem**: Loading `matches.csv` (900MB+) is slow and triggers TypeWarnings.
*   **Fix**: 
    1.  Optimize Dtypes (Category vs Object).
    2.  Migrate to **SQLite** or **Parquet** for instant queries without loading 1GB into RAM.

## 5. Future Roadmap (The "Wow" Factor)

1.  **Streak Analysis**: "Has anyone scored 3 centuries in a row?"
2.  **Phase Analysis**: "Strike rate in the Powerplay vs Death Overs."
3.  **Head-to-Head**: "Kohli vs Anderson: Who wins?"
4.  **Web Dashboard**: A React/Next.js UI where users type a claim and see a "Truth-O-Meter" animate from Red (False) to Green (True).

---
*Created: Jan 2026*
