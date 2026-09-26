import os
import json
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

API_KEY = os.environ.get("ODDS_API_KEY")
START_DATE = "2025-10-10T12:00:00Z"
END_DATE = "2026-04-15T12:00:00Z"
INITIAL_BANKROLL = 1000.0
FLAT_BET_AMOUNT = 25.0

TEAM_ABBREVS = {
    "Anaheim Ducks": "ANA", "Boston Bruins": "BOS", "Buffalo Sabres": "BUF",
    "Calgary Flames": "CGY", "Carolina Hurricanes": "CAR", "Chicago Blackhawks": "CHI",
    "Colorado Avalanche": "COL", "Columbus Blue Jackets": "CBJ", "Dallas Stars": "DAL",
    "Detroit Red Wings": "DET", "Edmonton Oilers": "EDM", "Florida Panthers": "FLA",
    "Los Angeles Kings": "LAK", "Minnesota Wild": "MIN", "Montréal Canadiens": "MTL", "Montreal Canadiens": "MTL",
    "Nashville Predators": "NSH", "New Jersey Devils": "NJD", "New York Islanders": "NYI",
    "New York Rangers": "NYR", "Ottawa Senators": "OTT", "Philadelphia Flyers": "PHI",
    "Pittsburgh Penguins": "PIT", "San Jose Sharks": "SJS", "Seattle Kraken": "SEA",
    "St. Louis Blues": "STL", "St Louis Blues": "STL", "Tampa Bay Lightning": "TBL", 
    "Toronto Maple Leafs": "TOR", "Utah Hockey Club": "UTA", "Vancouver Canucks": "VAN", 
    "Vegas Golden Knights": "VGK", "Washington Capitals": "WSH", "Winnipeg Jets": "WPG"
}

def american_to_decimal(odds):
    return (odds / 100) + 1 if odds > 0 else (100 / abs(odds)) + 1

def devig_proportional(odds1, odds2):
    dec1, dec2 = american_to_decimal(odds1), american_to_decimal(odds2)
    overround = (1 / dec1) + (1 / dec2)
    return (1 / dec1) / overround, (1 / dec2) / overround

def run_backtest():
    if not API_KEY:
        print("Error: ODDS_API_KEY secret not found.")
        return
        
    current_date = datetime.strptime(START_DATE, "%Y-%m-%dT%H:%M:%SZ")
    end_dt = datetime.strptime(END_DATE, "%Y-%m-%dT%H:%M:%SZ")
    
    bankroll = INITIAL_BANKROLL
    total_wagered = 0.0
    bet_history = []
    
    print("--- INITIATING REAL NHL BACKTEST ---")
    
    while current_date <= end_dt:
        date_str = current_date.strftime("%Y-%m-%dT%H:%M:%SZ")
        nhl_date_str = current_date.strftime("%Y-%m-%d")
        
        url = f"https://api.the-odds-api.com/v4/historical/sports/icehockey_nhl/odds?apiKey={API_KEY}&regions=us,eu&markets=h2h,spreads,totals&date={date_str}"
        response = requests.get(url)
        
        if response.status_code != 200:
            current_date += timedelta(days=1)
            continue
            
        games = response.json().get('data', [])
        
        try:
            score_res = requests.get(f"https://api-web.nhle.com/v1/score/{nhl_date_str}", timeout=10).json()
            box_scores = {
                f"{g['awayTeam']['abbrev']}_{g['homeTeam']['abbrev']}": {
                    "away_pts": g['awayTeam']['score'],
                    "home_pts": g['homeTeam']['score']
                }
                for g in score_res.get('games', []) if g['gameState'] in ['FINAL', 'OFF']
            }
        except Exception:
            box_scores = {}
            
        daily_bets = 0
            
        for game in games:
            away_team = game['away_team']
            home_team = game['home_team']
            
            away_abbrev = TEAM_ABBREVS.get(away_team)
            home_abbrev = TEAM_ABBREVS.get(home_team)
            game_key = f"{away_abbrev}_{home_abbrev}"
            
            if game_key not in box_scores:
                continue 
                
            actual_away_pts = box_scores[game_key]['away_pts']
            actual_home_pts = box_scores[game_key]['home_pts']
            
            pinny = next((b for b in game.get('bookmakers', []) if b['key'] == 'pinnacle'), None)
            if not pinny: 
                continue
            
            true_probs = {}
            for market in pinny['markets']:
                m_type = market['key']
                if len(market['outcomes']) == 2:
                    p1, p2 = market['outcomes'][0], market['outcomes'][1]
                    t1, t2 = devig_proportional(p1['price'], p2['price'])
                    true_probs[f"{m_type}_{p1['name']}_{p1.get('point', '')}"] = t1
                    true_probs[f"{m_type}_{p2['name']}_{p2.get('point', '')}"] = t2
            
            for book in game.get('bookmakers', []):
                if book['key'] in ['draftkings', 'fanduel', 'betmgm', 'caesars']:
                    for market in book['markets']:
                        m_type = market['key']
                        market_label = 'Moneyline' if m_type == 'h2h' else 'Puck Line' if m_type == 'spreads' else 'Totals'

                        for outcome in market['outcomes']:
                            name = outcome['name']
                            odds = outcome['price']
                            point = outcome.get('point', '')
                            
                            tp_key = f"{m_type}_{name}_{point}"
                            if tp_key in true_probs:
                                true_p = true_probs[tp_key]
                                retail_dec = american_to_decimal(odds)
                                ev_pct = (true_p * retail_dec - 1) * 100
                                
                                if ev_pct > 0.5:
                                    won_bet = False
                                    is_push = False
                                    
                                    if m_type == 'h2h':
                                        won_bet = actual_away_pts > actual_home_pts if name == away_team else actual_home_pts > actual_away_pts
                                    elif m_type == 'spreads':
                                        if name == away_team:
                                            won_bet = (actual_away_pts + point) > actual_home_pts
                                            is_push = (actual_away_pts + point) == actual_home_pts
                                        else:
                                            won_bet = (actual_home_pts + point) > actual_away_pts
                                            is_push = (actual_home_pts + point) == actual_away_pts
                                    elif m_type == 'totals':
                                        combined = actual_away_pts + actual_home_pts
                                        if combined == point:
                                            is_push = True
                                        elif name == 'Over':
                                            won_bet = combined > point
                                        elif name == 'Under':
                                            won_bet = combined < point
                                        
                                    if not is_push:
                                        profit = (FLAT_BET_AMOUNT * (retail_dec - 1)) if won_bet else -FLAT_BET_AMOUNT
                                        bankroll += profit
                                        total_wagered += FLAT_BET_AMOUNT
                                        daily_bets += 1
                                        
                                        bet_history.append({
                                            'Date': nhl_date_str,
                                            'Market': market_label,
                                            'EV_Pct': ev_pct,
                                            'Profit': profit,
                                            'Won': won_bet
                                        })
                                        
        if daily_bets > 0:
            print(f"[{nhl_date_str}] Processed {daily_bets} wagers.")
        current_date += timedelta(days=1)
        
    df = pd.DataFrame(bet_history)
    
    if df.empty:
        print("\nNo bets recorded.")
        output = {"total_bets": 0, "total_wagered": 0.0, "net_profit": 0.0, "roi": 0.0, "buckets": [], "markets": []}
        with open('backtest_results.json', 'w') as f:
            json.dump(output, f, indent=4)
        return

    net_profit = bankroll - INITIAL_BANKROLL
    roi = (net_profit / total_wagered * 100) if total_wagered > 0 else 0.0
    
    # 1. Bucket breakdown
    bins = [0, 2.0, 4.0, 6.0, np.inf]
    labels = ['0.0% - 2.0%', '2.0% - 4.0%', '4.0% - 6.0%', '6.0%+']
    df['EV_Bucket'] = pd.cut(df['EV_Pct'], bins=bins, labels=labels)
    
    bucket_data = []
    for label, group in df.groupby('EV_Bucket', observed=False):
        b_bets = len(group)
        b_profit = group['Profit'].sum() if b_bets > 0 else 0.0
        b_roi = (b_profit / (b_bets * FLAT_BET_AMOUNT) * 100) if b_bets > 0 else 0.0
        bucket_data.append({
            "range": label, "bets": int(b_bets), "profit": round(float(b_profit), 2), "roi": round(float(b_roi), 2)
        })

    # 2. Market breakdown
    market_data = []
    for label, group in df.groupby('Market'):
        m_bets = len(group)
        m_profit = group['Profit'].sum() if m_bets > 0 else 0.0
        m_roi = (m_profit / (m_bets * FLAT_BET_AMOUNT) * 100) if m_bets > 0 else 0.0
        market_data.append({
            "type": str(label), "bets": int(m_bets), "profit": round(float(m_profit), 2), "roi": round(float(m_roi), 2)
        })

    output = {
        "total_bets": len(df),
        "total_wagered": total_wagered,
        "net_profit": round(net_profit, 2),
        "roi": round(roi, 2),
        "buckets": bucket_data,
        "markets": market_data
    }

    with open('backtest_results.json', 'w') as f:
        json.dump(output, f, indent=4)
        
    print("\nBacktest completed successfully.")

if __name__ == "__main__":
    run_backtest()
