def generate_insight(subject: str, metric: str, filters: dict, 
                     real_val: float, baseline_val: float) -> str | None:
    """
    Generates intelligent natural-language insights comparing filtered metrics
    against career baselines natively, similar to CricViz or IBM Watson Analytics.
    """
    if real_val is None or baseline_val is None or baseline_val == 0:
        return None
        
    NON_PERCENTAGE_METRICS = {
        "total runs", "wickets", "runs conceded", "extras", "balls", "milestone"
    }
    
    metric_l = str(metric).lower()
    if any(m in metric_l for m in NON_PERCENTAGE_METRICS):
        return None # Rate/efficiency metrics only for relational percentages
        
    diff = real_val - baseline_val
    pct_diff = (abs(diff) / baseline_val) * 100

    if pct_diff < 5: 
        return None # No statistical insight if negligible deviation

    # Reconstruct readable context
    context = []
    if filters.get("venue_name"): context.append(f"at {filters['venue_name']}")
    elif filters.get("country"): context.append(f"in {filters['country']}")
    elif "away" in str(filters.get("home_away", "")).lower(): context.append("in Away conditions")
    
    if filters.get("innings"): 
        i_str = str(filters["innings"])
        suffix = "st" if i_str=="1" else "nd" if i_str=="2" else "rd" if i_str=="3" else "th"
        context.append(f"in the {i_str}{suffix} innings")
    
    if filters.get("match_phase"): context.append(f"in the {filters['match_phase']} overs")
    if filters.get("format"): context.append(f"in {filters['format']}s")
    if filters.get("opposition"): context.append(f"against {filters['opposition']}")
    if filters.get("bowler_type"): context.append(f"against {filters['bowler_type']}")
    
    ctx_str = " ".join(context) if context else "under these specific conditions"

    LOWER_IS_BETTER = ["economy", "bowling average", "bowling strike rate"]
    is_lower_better = any(m in metric_l for m in LOWER_IS_BETTER)

    direction = "higher" if diff > 0 else "lower"
    
    if diff > 0:
        performance_state = "worse" if is_lower_better else "better"
    else:
        performance_state = "better" if is_lower_better else "worse"
        
    magnitude = "slightly"
    if pct_diff > 15: magnitude = "significantly"
    if pct_diff > 35: magnitude = "exceptionally"
    if pct_diff > 50: magnitude = "monumentally"

    # Edge case: economy rate should be reported precisely to 2 decimals
    r_val = f"{real_val:.2f}" if "economy" in metric_l else f"{real_val:.1f}"
    b_val = f"{baseline_val:.2f}" if "economy" in metric_l else f"{baseline_val:.1f}"

    lines = [
        "==================================================",
        "  🧠 AI INSIGHT DETECTED",
        "==================================================",
        f"  {subject}'s {metric} {ctx_str} is {r_val},",
        f"  which is {pct_diff:.1f}% {direction} than their career baseline ({b_val}).",
        f"  ",
        f"  Conclusion:",
        f"  They perform {magnitude} {performance_state} {ctx_str}.",
        "==================================================\n"
    ]
    
    return "\n".join(lines)
