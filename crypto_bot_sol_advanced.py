import ccxt
import pandas as pd
import numpy as np
from datetime import datetime
import time
import os
import requests
from typing import Optional

class AdvancedTradingBot:
    def __init__(self, exchange_name: str = "kraken", api_key: str = None, 
                 api_secret: str = None, symbol: str = "SOL/USDT", 
                 account_type: str = "paper", starting_balance: float = 1000):
        
        self.exchange_name = exchange_name.lower()
        self.symbol = symbol
        self.account_type = account_type
        
        # Telegram setup
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        
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
        
        # RSI Settings - More aggressive for SOL (high volatility)
        self.rsi_period = 14
        self.rsi_oversold = 25
        self.rsi_overbought = 75
        
        # Moving Average Settings
        self.sma_short = 20
        self.sma_long = 50
        
        # Support/Resistance
        self.lookback_period = 20
        
        # Volume Settings
        self.volume_threshold = 1.5
        
        # Risk Management
        self.initial_position_size = 0.90
        self.dynamic_stop_loss_base = 3.0
        self.trailing_stop_percent = 4.0
        
        self.current_position = None
        self.entry_price = None
        self.position_high = None
        self.trade_history = []
        self.daily_pnl = 0
        self.trades_today = 0
    
    def send_telegram_alert(self, message: str):
        """Send Telegram alert"""
        if not self.telegram_token or not self.telegram_chat_id:
            return
        
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            requests.post(url, data=data, timeout=5)
        except Exception as e:
            print(f"✗ Telegram error: {e}")
    
    def calculate_rsi(self, prices, period: int = 14) -> Optional[float]:
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
    
    def calculate_support_resistance(self, df):
        if len(df) < self.lookback_period:
            return None, None
        
        support = df['low'].tail(self.lookback_period).min()
        resistance = df['high'].tail(self.lookback_period).max()
        
        return support, resistance
    
    def calculate_volume_signal(self, df):
        if len(df) < 20:
            return False
        
        avg_volume = df['volume'].tail(20).mean()
        current_volume = df['volume'].iloc[-1]
        
        return current_volume > (avg_volume * self.volume_threshold)
    
    def calculate_volatility(self, df):
        if len(df) < 20:
            return 0.02
        
        returns = df['close'].pct_change().tail(20)
        volatility = returns.std()
        
        return max(0.01, min(volatility, 0.08))
    
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
        if df is None or len(df) < self.sma_long:
            return 'HOLD'
        
        # Calculate indicators
        rsi = self.calculate_rsi(df['close'].values, self.rsi_period)
        
        if rsi is None:
            return 'HOLD'
        
        sma_short = df['close'].tail(self.sma_short).mean()
        sma_long = df['close'].tail(self.sma_long).mean()
        support, resistance = self.calculate_support_resistance(df)
        volume_signal = self.calculate_volume_signal(df)
        
        current_price = df['close'].iloc[-1]
        
        # BUY SIGNAL
        if self.current_position is None:
            buy_signals = 0
            
            if rsi < self.rsi_oversold:
                buy_signals += 1
            
            if sma_short > sma_long:
                buy_signals += 1
            
            if support and current_price < support * 1.02:
                buy_signals += 1
            
            if volume_signal:
                buy_signals += 1
            
            if buy_signals >= 3:
                return 'BUY'
        
        # SELL SIGNAL
        if self.current_position is not None:
            current_price = df['close'].iloc[-1]
            pnl_percent = ((current_price - self.entry_price) / self.entry_price) * 100
            
            sell_signals = 0
            
            if rsi > self.rsi_overbought:
                sell_signals += 1
            
            if sma_short < sma_long:
                sell_signals += 1
            
            if resistance and current_price > resistance * 0.98:
                sell_signals += 1
            
            # Stop loss
            volatility = self.calculate_volatility(df)
            dynamic_stop = self.dynamic_stop_loss_base + (volatility * 100)
            if pnl_percent < -dynamic_stop:
                return 'SELL'
            
            # Take profit (higher for SOL)
            if pnl_percent > 15:
                return 'SELL'
            
            # Trailing stop
            if self.position_high:
                trailing_loss = ((self.position_high - current_price) / self.position_high) * 100
                if trailing_loss > self.trailing_stop_percent:
                    return 'SELL'
            
            if sell_signals >= 2:
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
        
        position_value = available_balance * self.initial_position_size
        
        if position_value < 10:
            return False
        
        amount = position_value / current_price
        
        if self.account_type == "paper":
            self.current_position = {'amount': amount, 'entry_price': current_price}
            self.entry_price = current_price
            self.position_high = current_price
            self.paper_balance -= position_value
            self.trades_today += 1
            
            message = f"🟢 <b>SOL BUY SIGNAL</b> ☀️\n\n"
            message += f"Coin: {self.symbol}\n"
            message += f"Amount: {amount:.4f}\n"
            message += f"Price: ${current_price:.2f}\n"
            message += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            self.send_telegram_alert(message)
            print(f"🟢 BUY: {amount:.4f} {self.symbol.split('/')[0]} @ ${current_price:.2f}")
        else:
            try:
                order = self.exchange.create_market_buy_order(self.symbol, amount)
                self.current_position = order
                self.entry_price = current_price
                self.position_high = current_price
                self.trades_today += 1
                
                message = f"🟢 <b>SOL BUY SIGNAL</b> ☀️\n\n"
                message += f"Coin: {self.symbol}\n"
                message += f"Amount: {amount:.4f}\n"
                message += f"Price: ${current_price:.2f}\n"
                message += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                
                self.send_telegram_alert(message)
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
                
                message = f"🔴 <b>SOL SELL SIGNAL</b> ☀️\n\n"
                message += f"Coin: {self.symbol}\n"
                message += f"Amount: {amount:.4f}\n"
                message += f"Sell Price: ${current_price:.2f}\n"
                message += f"Entry Price: ${self.entry_price:.2f}\n"
                message += f"<b>PnL: ${pnl:.2f} ({pnl_percent:.2f}%)</b>\n"
                message += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                
                self.send_telegram_alert(message)
                print(f"🔴 SELL: {amount:.4f} {self.symbol.split('/')[0]} @ ${current_price:.2f}")
                print(f"   PnL: ${pnl:.2f} ({pnl_percent:.2f}%)")
            else:
                amount = self.current_position.get('amount', 0)
                order = self.exchange.create_market_sell_order(self.symbol, amount)
                pnl = (current_price - self.entry_price) * amount
                self.daily_pnl += pnl
                
                pnl_percent = (pnl / (self.entry_price * amount)) * 100
                message = f"🔴 <b>SOL SELL SIGNAL</b> ☀️\n\n"
                message += f"Coin: {self.symbol}\n"
                message += f"Amount: {amount:.4f}\n"
                message += f"Sell Price: ${current_price:.2f}\n"
                message += f"Entry Price: ${self.entry_price:.2f}\n"
                message += f"<b>PnL: ${pnl:.2f} ({pnl_percent:.2f}%)</b>\n"
                message += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                
                self.send_telegram_alert(message)
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
            self.position_high = None
            return True
        except Exception as e:
            print(f"✗ Sell error: {e}")
            return False
    
    def run_loop(self, interval_seconds: int = 60, max_iterations: Optional[int] = None):
        iteration = 0
        print(f"\n☀️ Starting ADVANCED SOL trading loop ({self.symbol})...")
        print(f"   Account type: {self.account_type.upper()}")
        print(f"   Strategy: Multi-Indicator + Risk Management (AGGRESSIVE)")
        print(f"   Telegram alerts: {'ENABLED ✅' if self.telegram_token else 'DISABLED'}")
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
        
        print("\n" + "="*60)
        print("☀️ ADVANCED SOL BOT - TRADING SUMMARY")
        print("="*60)
        print(f"Starting balance: ${self.starting_balance:.2f}")
        print(f"Current balance: ${current_balance:.2f}")
        print(f"Total PnL: ${total_pnl:.2f} ({pnl_percent:.2f}%)")
        print(f"Trades completed: {len(self.trade_history)}")
        if self.current_position:
            print(f"Status: POSITION OPEN")
        print("="*60 + "\n")

if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    API_KEY = os.getenv("KRAKEN_API_KEY", "")
    API_SECRET = os.getenv("KRAKEN_API_SECRET", "")
    ACCOUNT_TYPE = os.getenv("ACCOUNT_TYPE", "paper")
    
    bot = AdvancedTradingBot(
        exchange_name="kraken",
        api_key=API_KEY,
        api_secret=API_SECRET,
        symbol="SOL/USDT",
        account_type=ACCOUNT_TYPE
    )
    
    bot.run_loop(interval_seconds=60)
