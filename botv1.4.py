# Kraken Trade Bot – Vollständige, stabile Version mit GUI, Handelslogik, Signalen und Charts

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton, QLabel,
    QLineEdit, QTextEdit, QTableWidget, QTableWidgetItem, QHBoxLayout, QMessageBox,
    QListWidget, QListWidgetItem, QInputDialog
)
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from datetime import datetime
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np
import requests
import time
import sys
import hmac
import hashlib
import base64
import urllib.parse
import csv

# ----------------- Globale Konfiguration -----------------
TRADE_PAIRS = {"XETHZEUR": 0.01, "SOLEUR": 0.2}
PRICE_HISTORY = {pair: [] for pair in TRADE_PAIRS}
SIMUL_ASSETS = {pair: 0.0 for pair in TRADE_PAIRS}
SIMUL_WALLET_VALUE = 1000.0
TRADES = []
SIMUL = True
STOP_LOSS_DYNAMIC = 0.02
TAKE_PROFIT_DYNAMIC = 0.03
REENTRY_THRESHOLD = 0.01
RSI_PERIOD = 14
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2.0
KRAKEN_API_URL = "https://api.kraken.com"
API_KEY = ""
API_SECRET = ""
LAST_TRADE_PRICE = {}

# ----------------- BotThread -----------------
class BotThread(QThread):
    update_gui = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.running = True

    def run(self):
        print("[DEBUG] BotThread gestartet.")
        while self.running:
            try:
                for pair, amount in TRADE_PAIRS.items():
                    price = fetch_price(pair)
                    if price is None:
                        print(f"[WARNING] Kein Preis für {pair}")
                        continue

                    PRICE_HISTORY[pair].append(price)
                    if len(PRICE_HISTORY[pair]) > 100:
                        PRICE_HISTORY[pair].pop(0)

                    rsi = calculate_rsi(PRICE_HISTORY[pair])
                    sma, upper, lower = calculate_bollinger(PRICE_HISTORY[pair])

                    if SIMUL and SIMUL_ASSETS[pair] == 0.0:
                        execute_trade(pair, "buy", amount, price, "Initialkauf (SIMUL)")
                        continue

                    if rsi is not None:
                        if rsi < 30 and price < lower:
                            execute_trade(pair, "buy", amount, price, f"Signal: RSI={rsi:.2f}, BB-Low={lower:.2f}")
                        elif rsi > 70 and price > upper:
                            execute_trade(pair, "sell", amount, price, f"Signal: RSI={rsi:.2f}, BB-High={upper:.2f}")

                self.update_gui.emit()
                time.sleep(5)
            except Exception as e:
                print(f"[ERROR] in BotThread.run: {e}")

    def stop(self):
        self.running = False

# ----------------- Hilfsfunktionen -----------------
def fetch_price(pair):
    try:
        url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
        response = requests.get(url)
        data = response.json()
        return float(data["result"][pair]["c"][0])
    except Exception as e:
        print(f"[ERROR] Preisabfrage fehlgeschlagen für {pair}: {e}")
        return None

def calculate_rsi(prices, period=RSI_PERIOD):
    if len(prices) < period:
        return None
    deltas = np.diff(prices)
    gains = deltas[deltas > 0]
    losses = -deltas[deltas < 0]
    avg_gain = np.mean(gains) if len(gains) > 0 else 0
    avg_loss = np.mean(losses) if len(losses) > 0 else 1e-10
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_bollinger(prices, period=BOLLINGER_PERIOD):
    if len(prices) < period:
        return None, None, None
    sma = np.mean(prices[-period:])
    std = np.std(prices[-period:])
    return sma, sma + BOLLINGER_STD * std, sma - BOLLINGER_STD * std

def execute_trade(pair, side, volume, price, reason):
    global SIMUL_WALLET_VALUE
    if SIMUL:
        if side == "buy" and SIMUL_WALLET_VALUE >= volume * price:
            SIMUL_WALLET_VALUE -= volume * price
            SIMUL_ASSETS[pair] += volume
            msg = f"[SIMUL] BUY {volume} {pair} @ {price:.2f} — Grund: {reason}"
        elif side == "sell" and SIMUL_ASSETS[pair] >= volume:
            SIMUL_ASSETS[pair] -= volume
            SIMUL_WALLET_VALUE += volume * price
            msg = f"[SIMUL] SELL {volume} {pair} @ {price:.2f} — Grund: {reason}"
        else:
            msg = f"[SIMUL] Nicht genug {'EUR' if side == 'buy' else pair} für {side.upper()}"
        print("[DEBUG] " + msg)
        TRADES.append(msg)

# ----------------- GUI Hauptfenster -----------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kraken Trade Bot")
        self.setGeometry(100, 100, 1000, 600)
        self.bot_thread = None
        self.chart_window = None

        main_layout = QHBoxLayout()
        self.left_layout = QVBoxLayout()

        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("API Key")
        self.left_layout.addWidget(self.api_key_input)

        self.api_secret_input = QLineEdit()
        self.api_secret_input.setPlaceholderText("API Secret")
        self.left_layout.addWidget(self.api_secret_input)

        self.save_button = QPushButton("Save API Keys")
        self.save_button.clicked.connect(self.save_keys)
        self.left_layout.addWidget(self.save_button)

        self.mode_button = QPushButton("Switch to Real Mode")
        self.mode_button.clicked.connect(self.toggle_mode)
        self.left_layout.addWidget(self.mode_button)

        self.add_pair_btn = QPushButton("Add Pair")
        self.add_pair_btn.clicked.connect(self.add_pair)
        self.left_layout.addWidget(self.add_pair_btn)

        self.del_pair_btn = QPushButton("Delete Pair")
        self.del_pair_btn.clicked.connect(self.delete_pair)
        self.left_layout.addWidget(self.del_pair_btn)

        self.start_button = QPushButton("Start Bot")
        self.start_button.clicked.connect(self.start_bot)
        self.left_layout.addWidget(self.start_button)

        self.stop_button = QPushButton("Stop Bot")
        self.stop_button.clicked.connect(self.stop_bot)
        self.left_layout.addWidget(self.stop_button)

        self.chart_button = QPushButton("Show Charts")
        self.chart_button.clicked.connect(self.show_chart)
        self.left_layout.addWidget(self.chart_button)

        self.status_display = QTextEdit()
        self.status_display.setReadOnly(True)
        self.left_layout.addWidget(self.status_display)

        self.portfolio_table = QTableWidget()
        self.portfolio_table.setColumnCount(2)
        self.portfolio_table.setHorizontalHeaderLabels(["Asset", "Amount"])

        self.trade_list = QListWidget()

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("Portfolio:"))
        right_layout.addWidget(self.portfolio_table)
        right_layout.addWidget(QLabel("Trades:"))
        right_layout.addWidget(self.trade_list)

        main_layout.addLayout(self.left_layout)
        main_layout.addLayout(right_layout)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

    def save_keys(self):
        global API_KEY, API_SECRET
        API_KEY = self.api_key_input.text()
        API_SECRET = self.api_secret_input.text()
        self.status_display.append("[INFO] API Keys saved.")

    def toggle_mode(self):
        global SIMUL, SIMUL_ASSETS, SIMUL_WALLET_VALUE
        if SIMUL is False:
            SIMUL_ASSETS = {pair: 0.0 for pair in TRADE_PAIRS}
            SIMUL_WALLET_VALUE = 1000.0
        SIMUL = not SIMUL
        mode = "Simulation" if SIMUL else "Real"
        self.status_display.append(f"[INFO] Switched to {mode} Mode.")
        self.mode_button.setText(f"Switch to {'Real' if SIMUL else 'Simulation'} Mode")

    def add_pair(self):
        pair, ok = QInputDialog.getText(self, "Add Pair", "Enter Kraken Pair (e.g., XXBTZEUR):")
        if ok and pair:
            if pair not in TRADE_PAIRS:
                TRADE_PAIRS[pair] = 0.01
                PRICE_HISTORY[pair] = []
                SIMUL_ASSETS[pair] = 0.0
                self.status_display.append(f"[INFO] Added pair: {pair}")

    def delete_pair(self):
        pairs = list(TRADE_PAIRS.keys())
        pair, ok = QInputDialog.getItem(self, "Delete Pair", "Select pair to delete:", pairs, 0, False)
        if ok and pair:
            TRADE_PAIRS.pop(pair, None)
            PRICE_HISTORY.pop(pair, None)
            SIMUL_ASSETS.pop(pair, None)
            self.status_display.append(f"[INFO] Deleted pair: {pair}")

    def start_bot(self):
        if not self.bot_thread or not self.bot_thread.isRunning():
            self.bot_thread = BotThread()
            self.bot_thread.update_gui.connect(self.update_gui)
            self.bot_thread.start()
            self.status_display.append("[INFO] Bot gestartet.")

    def stop_bot(self):
        if self.bot_thread:
            self.bot_thread.stop()
            self.bot_thread.wait()
            self.status_display.append("[INFO] Bot gestoppt.")

    def show_chart(self):
        if not self.chart_window:
            self.chart_window = ChartWindow()
        self.chart_window.show()

    def update_gui(self):
        self.portfolio_table.setRowCount(0)
        for i, (asset, amount) in enumerate(SIMUL_ASSETS.items()):
            self.portfolio_table.insertRow(i)
            self.portfolio_table.setItem(i, 0, QTableWidgetItem(asset))
            self.portfolio_table.setItem(i, 1, QTableWidgetItem(str(round(amount, 4))))
        self.trade_list.clear()
        for trade in TRADES[-10:]:
            self.trade_list.addItem(QListWidgetItem(trade))


# ----------------- ChartWindow -----------------
class ChartWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Live Charts")
        layout = QVBoxLayout()
        self.canvas = FigureCanvas(Figure(figsize=(10, 4)))
        layout.addWidget(self.canvas)
        self.setLayout(layout)
        self.ax = self.canvas.figure.add_subplot(111)
        self.timer = QTimer()
        self.timer.timeout.connect(self.plot)
        self.timer.start(5000)

    def plot(self):
        self.ax.clear()
        for pair, prices in PRICE_HISTORY.items():
            if prices:
                self.ax.plot(prices[-100:], label=pair)
        self.ax.set_title("Price History (last 100 points)")
        self.ax.legend()
        self.canvas.draw()


# ----------------- Main -----------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
