import os
import json
import requests
import numpy as np
import pandas as pd

API_KEY = os.environ.get("ODDS_API_KEY")
ITERATIONS = 10000

# Dictionary to map MoneyPuck names to Odds API names
TEAM_MAP = {
    "Anaheim Ducks": "Anaheim Ducks", "Boston Bruins": "Boston Bruins", "Buffalo Sabres": "Buffalo Sabres",
    "Calgary Flames": "Calgary Flames", "Carolina Hurricanes": "Carolina Hurricanes", "Chicago Blackhawks": "Chicago Blackhawks",
    "Colorado Avalanche": "Colorado Avalanche", "Columbus Blue Jackets": "Columbus Blue Jackets", "Dallas Stars": "Dallas Stars",
    "Detroit Red Wings": "Detroit Red Wings", "Edmonton Oilers": "Edmonton Oilers", "Florida Panthers": "Florida Panthers",
    "Los Angeles Kings": "Los Angeles Kings", "Minnesota Wild": "Minnesota Wild", "Montreal Canadiens": "Montréal Canadiens",
    "Nashville Predators": "Nashville Predators", "New Jersey Devils": "New Jersey Devils", "New York Islanders": "New York Islanders",
    "New York Rangers": "New York Rangers", "Ottawa Senators": "Ottawa Senators", "Philadelphia Flyers": "Philadelphia Flyers",
    "Pittsburgh Penguins": "Pittsburgh Penguins", "San Jose Sharks": "San Jose Sharks", "Seattle Kraken": "Seattle Kraken",
    "St Louis Blues": "St. Louis Blues", "Tampa Bay Lightning": "Tampa Bay Lightning", "Toronto Maple Leafs": "Toronto Maple Leafs",
    "Utah Hockey Club": "Utah Hockey Club", "Vancouver Canucks": "Vancouver Canucks", "Vegas Golden Knights": "Vegas Golden Knights",
    "Washington Capitals": "Washington Capitals", "Winnipeg Jets": "Winnipeg Jets"
}

def american_to_decimal(odds):
    return (odds / 100) + 1 if odds > 0 else (100 / abs(odds)) + 1

def devig_proportional(odds1, odds2):
    dec1, dec2 = american_to_decimal(odds1), american_to_decimal(odds2)
    overround = (1 / dec1) + (1 / dec2)
    return (1 / dec1) / overround, (1 / dec2) / overround

def get_moneypuck_data():
    try:
        print("Fetching MoneyPuck Expected Goals data...")
        url = 'https://moneypuck.com/moneypuck/playerData/teams/teams.csv'
        df = pd.read_csv(url)
        # Filter for all situations in the most recent season
        df = df[(df['situation'] == 'all') & (df['season'] == df['season'].max())].copy()
        
        # Calculate per-game metrics
        df['xGF_per_game'] = df['xGoalsFor'] / df['games_played']
        df['xGA_per_game'] = df['xGoalsAgainst'] / df['games_played']
        
        # Map names to match Odds API
        df['team_name'] = df['name'].map(TEAM_MAP)
        return df.set_index('team_name'), df['xGF_per_game'].mean()
    except Exception as e:
        print(f"Failed to fetch MoneyPuck data: {e}")
        return None, 3.1 # Fallback league average goals

def simulate_nhl_game(away_stats, home_stats, league_avg, iterations=ITERATIONS):
    # Calculate expected goals for this specific matchup
    # Formula: (Team xGF) * (Opponent xGA) / League Avg
    away_exp_goals = (away_stats['xGF_per_game'] * home_stats['xGA_per_game']) / league_avg
    home_exp_goals = ((home_stats['xGF_per_game'] * away_stats['xGA_per_game']) / league_avg) + 0.25 # Home ice bump
    
    # Run Poisson Distribution
    away_sims = np.random.poisson(lam=away_exp_goals, size=iterations)
    home_sims = np.random.poisson(lam=home_exp_goals, size=iterations)
    
    # 1. Moneyline (Resolve ties via 50/50 OT coinflip)
    ties = home_sims == away_sims
    ot_home_wins = np.random.binomial(1, 0.5, size=ties.sum())
    
    home_wins = (home_sims > away_sims).sum() + ot_home_wins.sum()
    away_wins = iterations - home_wins
    
    # 2. Puck Line (-1.5 / +1.5)
    home_cover_pl = (home_sims - away_sims >= 2).sum() / iterations
    away_cover_pl = 1.0 - home_cover_pl

    return {
        "home_ml": home_wins / iterations,
        "away_ml": away_wins / iterations,
        "home_pl": home_cover_pl,
        "away_pl": away_cover_pl,
        "sim_scores": (away_sims, home_sims) # Pass raw scores to calculate dynamic Totals
    }

def process_market(market_data, bet_type, sim_probs, game_label, projections):
    if not market_data: return
    
    # Find Pinnacle to establish sharp truth
    pinny = next((b for b in market_data if b['key'] == 'pinnacle'), None)
    true_probs = {}
    
    if pinny:
        for market in pinny['markets']:
            if market['key'] == bet_type:
                p1, p2 = market['outcomes'][0], market['outcomes'][1]
                t1, t2 = devig_proportional(p1['price'], p2['price'])
                true_probs[p1['name']] = {"prob": t1, "point": p1.get('point')}
                true_probs[p2['name']] = {"prob": t2, "point": p2.get('point')}
                break

    # Compare against retail books
    for book in market_data:
        if book['key'] in ['draftkings', 'fanduel', 'betmgm', 'caesars']:
            for market in book['markets']:
                if market['key'] == bet_type:
                    for outcome in market['outcomes']:
                        name = outcome['name']
                        odds = outcome['price']
                        point = outcome.get('point')
                        
                        # Use Pinnacle true prob if available, otherwise use our Poisson simulation
                        if name in true_probs and true_probs[name].get('point') == point:
                            true_p = true_probs[name]['prob']
                        else:
                            # Dynamic simulation fallback for Totals based on exact line (e.g. 5.5 vs 6.5)
                            if bet_type == 'totals':
                                away_sims, home_sims = sim_probs['sim_scores']
                                combined_goals = away_sims + home_sims
                                if name == 'Over':
                                    true_p = (combined_goals > point).sum() / ITERATIONS
                                else:
                                    true_p = (combined_goals < point).sum() / ITERATIONS
                            elif bet_type == 'spreads':
                                true_p = sim_probs['home_pl'] if point < 0 else sim_probs['away_pl']
                            else: # h2h
                                true_p = sim_probs['home_ml'] if name == sim_probs['home_team'] else sim_probs['away_ml']
                        
                        ev_pct = (true_p * american_to_decimal(odds) - 1) * 100
                        if ev_pct > 0.5: # 0.5% threshold to filter noise
                            projections.append({
                                "game": game_label,
                                "bookmaker": book['title'],
                                "market": f"{name} {point if point else 'ML'}",
                                "odds": odds,
                                "true_prob": true_p * 100,
                                "edge": ev_pct
                            })

def main():
    projections = []
    
    try:
        mp_data, league_avg = get_moneypuck_data()
        
        odds_url = f"https://api.the-odds-api.com/v4/sports/icehockey_nhl/odds?regions=us,eu&markets=h2h,spreads,totals&oddsFormat=american&apiKey={API_KEY}"
        games = requests.get(odds_url, timeout=20).json()
        
        if not isinstance(games, list): return
        
        for game in games:
            away_team, home_team = game['away_team'], game['home_team']
            
            # Baseline stats if team is missing from MoneyPuck dict mapping
            away_stats = mp_data.loc[away_team] if mp_data is not None and away_team in mp_data.index else {'xGF_per_game': league_avg, 'xGA_per_game': league_avg}
            home_stats = mp_data.loc[home_team] if mp_data is not None and home_team in mp_data.index else {'xGF_per_game': league_avg, 'xGA_per_game': league_avg}
            
            sim_probs = simulate_nhl_game(away_stats, home_stats, league_avg)
            sim_probs['away_team'] = away_team
            sim_probs['home_team'] = home_team
            
            game_label = f"{away_team} @ {home_team}"
            
            # Process all 3 markets
            process_market(game.get('bookmakers'), 'h2h', sim_probs, game_label, projections)
            process_market(game.get('bookmakers'), 'spreads', sim_probs, game_label, projections)
            process_market(game.get('bookmakers'), 'totals', sim_probs, game_label, projections)
            
        projections = sorted(projections, key=lambda x: x['edge'], reverse=True)
        
    except Exception as e: print(f"Error: {e}")
    finally:
        with open('projections.json', 'w') as f:
            json.dump(projections, f, indent=4)

if __name__ == "__main__":
    main()
