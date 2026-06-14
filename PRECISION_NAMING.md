## Precision-first naming (scoped namespaces)

Use **scoped namespaces** instead of flat keys so the LLM and downstream code can’t confuse similar concepts.

### Namespaces

- **`subject.*`**: canonical identity
  - `subject.player_id`, `subject.team_id`, `subject.role_type`

- **`temporal.*`**: time precision
  - `temporal.season_year`, `temporal.date_range`, `temporal.match_id`

- **`spatial.*`**: location precision
  - `spatial.venue_name`, `spatial.country_iso`, `spatial.city`

- **`situational.*`**: match state
  - `situational.innings_no`, `situational.match_phase`, `situational.target_score`

- **`matchup.*`**: head-to-head
  - `matchup.vs_bowler_id`, `matchup.vs_arm_side`, `matchup.vs_pace_type`

### Migration guidance (current repo)

- Current parser output is **backwards compatible** (`filters.innings`, `filters.match_phase`, etc.).
- New development should introduce a `v2` parse output that emits the namespaces above, then map to `FilterSet` deterministically in `QueryPlanner`.

