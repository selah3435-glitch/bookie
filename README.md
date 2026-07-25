# Bookie - DraftKings Odds Scanner

A Python-based odds scanner for DraftKings with ESPN team-strength models to identify value bets across major US sports.

## Features

- Scans DraftKings odds across NFL, NCAAF, NBA, NCAAB, MLB, NHL, MMA, and MLS
- Uses ESPN standings data to estimate team strength (win percentage)
- Applies mathematical models (Elo-style and Poisson) to calculate true win probabilities
- Identifies bets with positive expected value (edge %) above a threshold
- Generates a CSV report with top recommendations

## Installation

1. Clone the repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up your API key:
   ```bash
   cp .env.example .env
   export ODDS_API_KEY=your_key_here
   ```

## Usage

Run the scanner:
```bash
python bookie.py
```

The script will:
- Fetch current DraftKings odds from The Odds API
- Load ESPN standings for strength models
- Identify value bets with edge % > 3.0%
- Output top 10 plays to console and CSV file

## Requirements

- Python 3.8+
- pandas
- requests
- The Odds API key (free tier available at https://the-odds-api.com)

## Configuration

Edit `bookie.py` to customize:
- `EDGE_THRESHOLD`: Minimum edge % to surface a bet (default: 3.0%)
- `SPORT_KEYS`: Which sports to scan
- `BOOKMAKER`: Sportsbook to scan (default: DraftKings)
- `REGIONS`: Geographic regions (default: US)
