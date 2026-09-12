import ccxt
import pandas as pd
import numpy as np
from datetime import datetime
import time
import os
from typing import Optional

class CryptoTradingBot:
    def __init__(self, exchange_name: str = "kraken", api_key: str = None, 
                 api_secret: str = None, symbol: str = "SOL/USDT", 
                 account_type: str = "paper", starting_balance: float = 1000):
        
        self.exchange_name = exchange_name.lower()
        self.symbol = symbol
        self.account_type = account_type
        
        if account_type == "paper":
            self.exchange = None
            self.paper_balance = starting_balance
            self.starting_balance = starting_balance
            print(f"✓ Paper trading initialized with ${starting_balance:.2f}")
        else:
            try:
                exchange_class = getattr(ccxt, self.exchange_name)
                self.exchange = exchange_class({
                    'apiKey': api_key or '',
                    'secret': api_secret or '',
                    'enableRateLimit': True
                })
                balance = self.exchange.fetch_balance()
                usdt_balance = balance.get('USDT', {}).get('free', 0)
                self.starting_balance = usdt_balance
                print(f"✓ Connected to {self.exchange_name}")
                print(f"  Starting balance: ${usdt_balance:.2f}")
            except Exception as e:
                print(f"✗ Connection error: {e}")
                raise
        
        # Aggressive for SOL (high volatility)
        self.rsi_period = 14
        self.rsi_oversold = 25
        self.rsi_overbought = 75
        self.position_size_percent = 0.95
        self.stop_loss_percent = 3
        self.take_profit_percent = 8
        
        self.current_position = None
        self.entry_price = None
        self.trade_history = []
        self.daily_pnl = 0
    
    def calculate_rsi(self, prices, period: int = 14) -> float:
        if len(prices) < period + 1:
            return None
        
        deltas = np.diff(prices[-period-1:])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        
        if avg_loss == 0:
            return 100 if avg_gain > 0 else 0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def fetch_candles(self, timeframe: str = '1h', limit: int = 100):
        try:
            if self.account_type == "paper":
                temp_exchange = ccxt.kraken()
                candles = temp_exchange.fetch_ohlcv(self.symbol, timeframe, limit=limit)
            else:
                candles = self.exchange.fetch_ohlcv(self.symbol, timeframe, limit=limit)
            
            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            print(f"✗ Error fetching candles: {e}")
            return None
    
    def get_signal(self, df):
        if df is None or len(df) < self.rsi_period + 1:
            return 'HOLD'
        
        rsi = self.calculate_rsi(df['close'].values, self.rsi_period)
        
        if rsi is None:
            return 'HOLD'
        
        if rsi < self.rsi_oversold and self.current_position is None:
            return 'BUY'
        
        if self.current_position is not None:
            current_price = df['close'].iloc[-1]
            pnl_percent = ((current_price - self.entry_price) / self.entry_price) * 100
            
            if rsi > self.rsi_overbought:
                return 'SELL'
            elif pnl_percent < -self.stop_loss_percent:
                return 'SELL'
            elif pnl_percent > self.take_profit_percent:
                return 'SELL'
        
        return 'HOLD'
    
    def execute_buy(self, current_price: float) -> bool:
        if self.current_position is not None:
            return False
        
        if self.account_type == "paper":
            available_balance = self.paper_balance
        else:
            try:
                available_balance = self.exchange.fetch_balance()['USDT']['free']
            except:
                return False
        
        position_value = available_balance * self.position_size_percent
        
        if position_value < 10:
            return False
        
        amount = position_value / current_price
        
        if self.account_type == "paper":
            self.current_position = {'amount': amount, 'entry_price': current_price}
            self.entry_price = current_price
            self.paper_balance -= position_value
            print(f"🟢 BUY: {amount:.4f} {self.symbol.split('/')[0]} @ ${current_price:.2f}")
        else:
            try:
                order = self.exchange.create_market_buy_order(self.symbol, amount)
                self.current_position = order
                self.entry_price = current_price
                print(f"🟢 BUY: {amount:.4f} {self.symbol.split('/')[0]} @ ${current_price:.2f}")
            except Exception as e:
                print(f"✗ Buy error: {e}")
                return False
        
        return True
    
    def execute_sell(self, current_price: float) -> bool:
        if self.current_position is None:
            return False
        
        try:
            if self.account_type == "paper":
                amount = self.current_position['amount']
                position_value = amount * current_price
                pnl = position_value - (amount * self.entry_price)
                pnl_percent = (pnl / (amount * self.entry_price)) * 100
                
                self.paper_balance += position_value
                self.daily_pnl += pnl
                
                print(f"🔴 SELL: {amount:.4f} {self.symbol.split('/')[0]} @ ${current_price:.2f}")
                print(f"   PnL: ${pnl:.2f} ({pnl_percent:.2f}%)")
            else:
                amount = self.current_position.get('amount', 0)
                order = self.exchange.create_market_sell_order(self.symbol, amount)
                pnl = (current_price - self.entry_price) * amount
                self.daily_pnl += pnl
                print(f"🔴 SELL: {amount:.4f} {self.symbol.split('/')[0]} @ ${current_price:.2f}")
                print(f"   PnL: ${pnl:.2f}")
            
            self.trade_history.append({
                'timestamp': datetime.now(),
                'action': 'SELL',
                'price': current_price,
                'pnl': pnl
            })
            
            self.current_position = None
            self.entry_price = None
            return True
        except Exception as e:
            print(f"✗ Sell error: {e}")
            return False
    
    def run_loop(self, interval_seconds: int = 60, max_iterations: Optional[int] = None):
        iteration = 0
        print(f"\n☀️ Starting SOL trading loop ({self.symbol})...")
        print(f"   Account type: {self.account_type.upper()}")
        print(f"   Interval: {interval_seconds}s\n")
        
        try:
            while True:
                iteration += 1
                
                if max_iterations and iteration > max_iterations:
                    print("✓ Reached max iterations")
                    break
                
                try:
                    df = self.fetch_candles('1h', limit=100)
                    if df is None:
                        time.sleep(interval_seconds)
                        continue
                    
                    current_price = df['close'].iloc[-1]
                    signal = self.get_signal(df)
                    rsi = self.calculate_rsi(df['close'].values, self.rsi_period)
                    
                    status = f"RSI: {rsi:.1f} | Price: ${current_price:.2f} | Signal: {signal}"
                    
                    if self.current_position:
                        pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100
                        status += f" | Open PnL: {pnl_pct:.2f}%"
                    
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {status}")
                    
                    if signal == 'BUY':
                        self.execute_buy(current_price)
                    elif signal == 'SELL':
                        self.execute_sell(current_price)
                    
                    time.sleep(interval_seconds)
                
                except KeyboardInterrupt:
                    print("\n⏹ Stopping bot")
                    break
                except Exception as e:
                    print(f"✗ Loop error: {e}")
                    time.sleep(interval_seconds)
        
        finally:
            self.print_summary()
    
    def print_summary(self):
        if self.account_type == "paper":
            current_balance = self.paper_balance
        else:
            try:
                current_balance = self.exchange.fetch_balance()['USDT']['free']
            except:
                current_balance = self.starting_balance
        
        total_pnl = current_balance - self.starting_balance
        pnl_percent = (total_pnl / self.starting_balance) * 100
        
        print("\n" + "="*50)
        print("☀️ SOL BOT - TRADING SUMMARY")
        print("="*50)
        print(f"Starting balance: ${self.starting_balance:.2f}")
        print(f"Current balance: ${current_balance:.2f}")
        print(f"Total PnL: ${total_pnl:.2f} ({pnl_percent:.2f}%)")
        print(f"Trades completed: {len(self.trade_history)}")
        if self.current_position:
            print(f"Status: POSITION OPEN")
        print("="*50 + "\n")

if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    # Get API keys from environment variables
    API_KEY = os.getenv("KRAKEN_API_KEY", "")
    API_SECRET = os.getenv("KRAKEN_API_SECRET", "")
    ACCOUNT_TYPE = os.getenv("ACCOUNT_TYPE", "paper")
    
    # Create bot
    bot = CryptoTradingBot(
        exchange_name="kraken",
        api_key=API_KEY,
        api_secret=API_SECRET,
        symbol="SOL/USDT",
        account_type=ACCOUNT_TYPE
    )
    
    # Run trading loop
    bot.run_loop(interval_seconds=60)
