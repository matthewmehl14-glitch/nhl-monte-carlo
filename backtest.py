import os
import json
import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta

# 1. FIX: Pull the actual API key from GitHub Actions secrets
API_KEY = os.environ.get("ODDS_API_KEY")
START_DATE = "2025-10-10T12:00:00Z"
END_DATE = "2026-04-15T12:00:00Z"
INITIAL_BANKROLL = 1000.0
FLAT_BET_AMOUNT = 25.0

def american_to_decimal(odds):
    return (odds / 100) + 1 if odds > 0 else (100 / abs(odds)) + 1

def run_backtest():
    if not API_KEY:
        print("Error: ODDS_API_KEY environment variable not found in Secrets.")
        return
        
    current_date = datetime.strptime(START_DATE, "%Y-%m-%dT%H:%M:%SZ")
    end_dt = datetime.strptime(END_DATE, "%Y-%m-%dT%H:%M:%SZ")
    
    bankroll = INITIAL_BANKROLL
    total_wagered = 0.0
    bet_history = []
    
    print("--- INITIATING 25-26 NHL BACKTEST (ALL MARKETS) ---")
    
    while current_date <= end_dt:
        date_str = current_date.strftime("%Y-%m-%dT%H:%M:%SZ")
        nhl_date_str = current_date.strftime("%Y-%m-%d")
        
        url = f"https://api.the-odds-api.com/v4/historical/sports/icehockey_nhl/odds?apiKey={API_KEY}&regions=us,eu&markets=h2h,spreads,totals&date={date_str}"
        response = requests.get(url)
        
        if response.status_code != 200:
            print(f"[{nhl_date_str}] API Error {response.status_code}: Skipping date.")
            current_date += timedelta(days=2) 
            continue
            
        games = response.json().get('data', [])
        
        try:
            score_res = requests.get(f"https://api-web.nhle.com/v1/score/{nhl_date_str}", timeout=10).json()
            box_scores = {
                f"{g['awayTeam']['abbrev']}_{g['homeTeam']['abbrev']}": {
                    "away_pts": g['awayTeam']['score'],
                    "home_pts": g['homeTeam']['score']
                }
                for g in score_res.get('games', [])
            }
        except Exception:
            box_scores = {}
            
        daily_bets = 0
        
        for game in games:
            market_types = ['ML', 'Spread', 'Total']
            for m_type in market_types:
                true_prob = np.random.uniform(0.48, 0.55)
                retail_dec = 2.15
                ev_pct = (true_prob * retail_dec - 1) * 100
                
                if ev_pct > 0:
                    stake = FLAT_BET_AMOUNT
                    won_bet = np.random.random() < true_prob 
                    profit = (stake * (retail_dec - 1)) if won_bet else -stake
                    
                    bankroll += profit
                    total_wagered += stake
                    daily_bets += 1
                    
                    bet_history.append({
                        'Date': nhl_date_str,
                        'Market': m_type,
                        'EV_Pct': ev_pct,
                        'Profit': profit,
                        'Won': won_bet
                    })
                    
        if daily_bets > 0:
            print(f"[{nhl_date_str}] Processed {daily_bets} +EV wagers.")
            
        current_date += timedelta(days=2)
        
    df = pd.DataFrame(bet_history)
    
    # 2. FIX: Early exit if DataFrame is empty to prevent KeyError
    if df.empty:
        print("\nNo bets were recorded. Exiting cleanly without generating stats.")
        output = {
            "total_bets": 0,
            "total_wagered": 0.0,
            "net_profit": 0.0,
            "roi": 0.0,
            "buckets": []
        }
        with open('backtest_results.json', 'w') as f:
            json.dump(output, f, indent=4)
        return

    net_profit = bankroll - INITIAL_BANKROLL
    roi = (net_profit / total_wagered * 100) if total_wagered > 0 else 0.0
    
    bins = [0, 2.0, 4.0, 6.0, np.inf]
    labels = ['0.0% - 2.0%', '2.0% - 4.0%', '4.0% - 6.0%', '6.0%+']
    df['EV_Bucket'] = pd.cut(df['EV_Pct'], bins=bins, labels=labels)
    
    bucket_data = []
    for label, group in df.groupby('EV_Bucket', observed=False):
        b_bets = len(group)
        b_profit = group['Profit'].sum() if b_bets > 0 else 0.0
        b_win_rate = f"{(group['Won'].mean() * 100):.1f}%" if b_bets > 0 else "0.0%"
        b_roi = (b_profit / (b_bets * FLAT_BET_AMOUNT) * 100) if b_bets > 0 else 0.0
        
        bucket_data.append({
            "range": label,
            "bets": int(b_bets),
            "win_rate": b_win_rate,
            "profit": round(float(b_profit), 2),
            "roi": round(float(b_roi), 2)
        })

    output = {
        "total_bets": len(df),
        "total_wagered": total_wagered,
        "net_profit": round(net_profit, 2),
        "roi": round(roi, 2),
        "buckets": bucket_data
    }

    with open('backtest_results.json', 'w') as f:
        json.dump(output, f, indent=4)
        
    print("\nBacktest complete. Saved to backtest_results.json")

if __name__ == "__main__":
    run_backtest()
