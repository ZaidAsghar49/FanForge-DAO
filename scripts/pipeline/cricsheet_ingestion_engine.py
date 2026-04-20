import json
import os
import pandas as pd
import glob
import hashlib
from datetime import datetime
from collections import Counter

# Import existing Identity Engine (assuming it's reliable, else we might need to wrap it)
# We need to make sure we can import it.
try:
    from identity.identity_engine import IdentityEngine
except ImportError:
    import sys
    sys.path.append(os.path.join(os.getcwd(), "scripts", "identity"))
    from identity_engine import IdentityEngine

class CricsheetIngestionEngine:
    def __init__(self, dataset_dir='Dataset', output_dir='.'):
        self.dataset_dir = dataset_dir
        self.output_dir = output_dir
        self.identity_engine = IdentityEngine()
        
        self.matches_buffer = []
        self.deliveries_buffer = []
        self.errors = []
        
        self.stats = {
            "ingestion_status": "starting",
            "matches_ingested": 0,
            "deliveries_ingested": 0,
            "players_mapped": 0,
            "venues_mapped": 0,
            "files_processed": 0,
            "files_failed": 0
        }
        
        # Venue map cache (could be externalized)
        self.venue_corrections = {
            "M Chinnaswamy Stadium": "M.Chinnaswamy Stadium",
            # Add more canonical mappings here as discovered
        }

    def generate_match_id(self, meta, filename):
        # Stable hash based on date, teams, and filename
        unique_str = f"{meta.get('dates', [''])[0]}_{'-'.join(sorted(meta.get('teams', [])))}_{filename}"
        return hashlib.md5(unique_str.encode('utf-8')).hexdigest()

    def normalize_match_info(self, data, filename):
        info = data.get('info', {})
        match_id = self.generate_match_id(info, filename)
        
        # Validation: Mandatory Fields
        mandatory = ['dates', 'teams', 'match_type', 'venue']
        for m in mandatory:
            if m not in info:
                raise ValueError(f"Missing mandatory metadata: {m}")
        
        dates = info.get('dates', [])
        date_str = dates[0] if dates else '1970-01-01'
        # Parse ISO date just to be sure, or keep string if valid
        
        teams = info.get('teams', [])
        if len(teams) < 2:
             raise ValueError("Match must have 2 teams")
             
        # Venue Normalization
        raw_venue = info.get('venue')
        venue = self.venue_corrections.get(raw_venue, raw_venue)
        
        # Toss
        toss = info.get('toss', {})
        outcome = info.get('outcome', {})
        
        match_record = {
            'match_id': match_id,
            'filename': filename,
            'date': date_str,
            'format': info.get('match_type'),
            'competition': info.get('competition', 'International'), # Default to Int
            'venue_name': venue,
            'city': info.get('city', ''),
            'country': self._resolve_country(venue, info.get('city')), # Placeholder logic
            'neutral_venue': info.get('neutral_venue', False),
            'team_a': teams[0],
            'team_b': teams[1],
            'toss_winner': toss.get('winner'),
            'toss_decision': toss.get('decision'),
            'winner': outcome.get('winner'),
            'margin_type': 'runs' if 'by' in outcome and 'runs' in outcome['by'] else 
                          'wickets' if 'by' in outcome and 'wickets' in outcome['by'] else 'tie/nr',
            'margin_value': outcome.get('by', {}).get('runs') or outcome.get('by', {}).get('wickets', 0),
            'overs_limit': info.get('overs', 20 if info.get('match_type') == 'T20' else 50) # Fallback heuristic
        }
        
        return match_record, match_id

    def _resolve_country(self, venue, city):
        # This checks simplistic mapping, ideally needs a full venue DB
        # For now, we trust the 'city' if present, or leave blank to be filled by post-processing
        # This engine honors specific inputs
        return city if city else "Unknown"

    def resolve_player(self, name):
        # Strict wrapper around IdentityEngine
        if not name: return None, "Unknown"
        
        result = self.identity_engine.resolve(name)
        
        # IdentityEngine returns a complex dict. We need to parse it.
        # Structure: {'players_detected': [{'player_id': '123', 'confidence': 1.0, 'ambiguous': False}], 'mapping_status': 'complete'}
        
        if result['mapping_status'] != 'complete':
            # Ambiguous or failed
             return None, f"Mapping failed: {name} ({result.get('notes')})"
             
        matches = result.get('players_detected', [])
        if not matches:
             return None, f"No player found for {name}"
             
        best_match = matches[0]
        
        # STRICT THRESHOLD ENFORCEMENT
        # Assuming IdentityEngine handles fuzzy cutoff, but we double check
        # NOTE: IdentityEngine in snippet didn't strictly expose 'score', but 'confidence'
        
        if best_match.get('ambiguous'):
            return None, f"Ambiguous match for {name}"
            
        # If we trust the confidence 1.0 logic from the engine (it does lookup)
        return best_match['player_id'], best_match['canonical_name']

    def normalize_deliveries(self, data, match_id):
        deliveries_clean = []
        player_map_cache = {} # Local cache for this match to speed up
        
        innings_list = data.get('innings', [])
        
        total_balls_checks = {} # innings_index -> count
        total_runs_checks = {} # innings_index -> sum
        
        errors = []

        for idx, inning in enumerate(innings_list):
            innings_num = idx + 1
            batting_team = inning.get('team')
            # Look for bowling team? Not always explicit in innings object, need to infer from match teams
            
            overs = inning.get('overs', [])
            
            ball_count = 0
            run_sum = 0
            
            for over_obj in overs:
                over_num = over_obj.get('over')
                
                for delivery in over_obj.get('deliveries', []):
                    # 1. Player Resolution
                    p_names = {
                        'batter': delivery['batter'],
                        'bowler': delivery['bowler'],
                        'non_striker': delivery['non_striker']
                    }
                    
                    p_ids = {}
                    
                    for role, name in p_names.items():
                        if name not in player_map_cache:
                            pid, cname = self.resolve_player(name)
                            if not pid:
                                errors.append(f"Player ID Resolution Failed: {name} ({cname})")
                                continue # We will fail the match later
                            player_map_cache[name] = {'id': pid, 'name': cname}
                        p_ids[role] = player_map_cache[name]['id']
                    
                    if errors: continue # Skip processing if identity failed
                    
                    # 2. Runs & Extras
                    runs = delivery.get('runs', {})
                    extras_obj = delivery.get('extras', {})
                    
                    runs_bat = runs.get('batter', 0)
                    runs_extras = runs.get('extras', 0)
                    runs_total = runs.get('total', 0)
                    
                    # 3. Wicket Logic
                    wickets = delivery.get('wickets', [])
                    wicket_type = None
                    dismissed_id = None
                    is_bowler_wicket = False
                    
                    if wickets:
                        w = wickets[0] # Handle first wicket (multi-wicket balls are theoretical unicorns but usually runouts)
                        wicket_type = w.get('kind')
                        player_out_name = w.get('player_out')
                        
                        # Resolve dismissed player
                        if player_out_name not in player_map_cache:
                            pid, cname = self.resolve_player(player_out_name)
                            if pid:
                                player_map_cache[player_out_name] = {'id': pid, 'name': cname}
                            else:
                                errors.append(f"Dismissed Player ID Failed: {player_out_name}")
                        
                        if errors: continue

                        dismissed_id = player_map_cache.get(player_out_name, {}).get('id')

                        # Law Compliance
                        if wicket_type in ['bowled', 'caught', 'lbw', 'stumped', 'hit wicket', 'caught and bowled']:
                            is_bowler_wicket = True
                        elif wicket_type in ['run out', 'retired hurt', 'obstructing the field', 'timed out']:
                             is_bowler_wicket = False
                    
                    # 4. Construct Record
                    record = {
                        'match_id': match_id,
                        'innings': innings_num,
                        'over': over_num,
                        'ball': -1, # Need to calculate ball number ideally, or just use order
                        'batting_team': batting_team,
                        'striker_id': p_ids.get('batter'),
                        'non_striker_id': p_ids.get('non_striker'),
                        'bowler_id': p_ids.get('bowler'),
                        'runs_off_bat': runs_bat,
                        'extras_wides': extras_obj.get('wides', 0),
                        'extras_noballs': extras_obj.get('noballs', 0),
                        'extras_byes': extras_obj.get('byes', 0),
                        'extras_legbyes': extras_obj.get('legbyes', 0),
                        'total_runs': runs_total,
                        'wicket_type': wicket_type,
                        'player_dismissed_id': dismissed_id,
                        'is_bowler_wicket': is_bowler_wicket
                    }
                    
                    deliveries_clean.append(record)
                    
                    # Validation Sums
                    if extras_obj.get('wides') or extras_obj.get('noballs'):
                        pass # Legal ball count doesn't increment usually (over doesn't progress)
                    else:
                        ball_count += 1
                    
                    run_sum += runs_total
            
            total_balls_checks[innings_num] = ball_count
            total_runs_checks[innings_num] = run_sum

        if errors:
            raise ValueError(f"Mapping Errors: {'; '.join(errors[:5])}...")

        return deliveries_clean, total_balls_checks, total_runs_checks

    def run(self, limit=None):
        files = glob.glob(os.path.join(self.dataset_dir, '*.json'))
        if limit:
            files = files[:limit]
            
        print(f"Starting ingestion of {len(files)} files...")
        self.stats['ingestion_status'] = 'running'
        
        for f in files:
            filename = os.path.basename(f)
            try:
                with open(f, 'r') as fp:
                    data = json.load(fp)
                
                # 1. Match Info
                match_rec, match_id = self.normalize_match_info(data, filename)
                
                # 2. Deliveries
                dels, balls_chk, runs_chk = self.normalize_deliveries(data, match_id)
                
                # 3. Quality Checks (Basic)
                # Compare runs_chk with match summary if available?
                # For now, we trust the sum is inherently correct from components, 
                # but we could verify against header if it existed.
                
                self.matches_buffer.append(match_rec)
                self.deliveries_buffer.extend(dels)
                
                self.stats['matches_ingested'] += 1
                self.stats['deliveries_ingested'] += len(dels)
                self.stats['files_processed'] += 1
                
            except Exception as e:
                self.stats['files_failed'] += 1
                self.errors.append({
                    "type": "processing_error",
                    "file": filename,
                    "description": str(e),
                    "severity": "high"
                })
                # print(f"Failed {filename}: {e}")

        self.finalize()

    def finalize(self):
        # Save Outputs
        print("Writing to CSV...")
        if self.matches_buffer:
            pd.DataFrame(self.matches_buffer).to_csv(os.path.join(self.output_dir, 'matches_normalized.csv'), index=False)
        
        if self.deliveries_buffer:
            pd.DataFrame(self.deliveries_buffer).to_csv(os.path.join(self.output_dir, 'deliveries_normalized.csv'), index=False)
            
        self.stats['ingestion_status'] = 'success' if self.stats['files_failed'] == 0 else 'partial'
        
        # Save Report
        with open(os.path.join(self.output_dir, 'ingestion_report.json'), 'w') as f:
            json.dump({
                "stats": self.stats,
                "errors": self.errors
            }, f, indent=2)
            
        print("Ingestion Complete.")
        print(json.dumps(self.stats, indent=2))

if __name__ == "__main__":
    engine = CricsheetIngestionEngine()
    engine.run(limit=50) # Strict test with small batch first
