# Kraken Trade Bot – Vollständige, stabile Version mit GUI, Handelslogik, Signalen und Charts

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton, QLabel,
    QLineEdit, QTextEdit, QTableWidget, QTableWidgetItem, QHBoxLayout, QMessageBox,
    QListWidget, QListWidgetItem, QInputDialog, QTabWidget, QCheckBox
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
LAST_TRADE_TIME = {}
LAST_BUY_PRICE = {}
TRADE_COOLDOWN_SECONDS = 60
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
LAST_TRADE_PRICE = {}
MIN_PROFIT_EUR = 10.0
MIN_PROFIT_PCT = 1.0
CHART_LINES = {}
LAST_LOGGED_TRADE = None
chart_window_instance = None
SAFE_BALANCES = {}  # Erlaubte Sockelbeträge geschützter Assets
SAFE_ASSET_ALLOW_SELL = {}  # Dict: Asset -> Checkbox true/false

# ----------------- Initialkäufe bei Botstart -----------------
def perform_initial_trades():
    global SIMUL_ASSETS, SIMUL_WALLET_VALUE
    for pair, amount in TRADE_PAIRS.items():
        price = fetch_price(pair)
        if price and SIMUL and SIMUL_ASSETS[pair] == 0.0:
            execute_trade(pair, "buy", amount, price, "Initialkauf (SIMUL)")
            LAST_BUY_PRICE[pair] = price
            LAST_TRADE_TIME[pair] = time.time()

# ----------------- Trendanalyse (lineare Regression) -----------------
def calculate_trend(prices):
    if len(prices) < 10:
        return 0
    x = np.arange(len(prices))
    y = np.array(prices)
    slope, _ = np.polyfit(x, y, 1)
    return slope


# ----------------- Fibonacci-Level -----------------
def calculate_fibonacci_levels(prices, lookback=50):
    """Calculate Fibonacci retracement levels with improved stability"""
    if len(prices) < 2:
        return None, None, None

    # Use most recent lookback period
    recent_prices = prices[-lookback:] if len(prices) > lookback else prices
    high = max(recent_prices)
    low = min(recent_prices)
    diff = high - low

    return (
        high,  # 0% level
        high - diff * 0.382,  # 38.2% level
        high - diff * 0.618  # 61.8% level
    )

# ----------------- Chart Linien aktualisieren inkl. Fibonacci -----------------
def update_chart_lines(ax, pair):
    prices = PRICE_HISTORY.get(pair, [])
    if not prices:
        return
    ax.clear()
    ax.plot(prices, label="Price", color="blue")
    ax.set_title(f"{pair} – letzte {len(prices)} Preise")

    # Dynamische Linien
    if prices:
        last_price = prices[-1]
        next_buy = last_price * (1 - REENTRY_THRESHOLD)
        next_sell = last_price * (1 + TAKE_PROFIT_DYNAMIC)
        stop_loss = last_price * (1 - STOP_LOSS_DYNAMIC)

        ax.axhline(y=next_buy, color="green", linestyle="--", label="Next Buy")
        ax.axhline(y=next_sell, color="red", linestyle="--", label="Next Sell")
        ax.axhline(y=stop_loss, color="orange", linestyle=":", label="Stop-Loss")

        # Fibonacci-Linien
        fib0, fib382, fib618 = calculate_fibonacci_levels(prices)
        if fib0: ax.axhline(y=fib0, color="purple", linestyle="--", linewidth=1, label="Fibo 0.0")
        if fib382: ax.axhline(y=fib382, color="purple", linestyle="--", linewidth=1, label="Fibo 38.2")
        if fib618: ax.axhline(y=fib618, color="purple", linestyle="--", linewidth=1, label="Fibo 61.8")

        # Trendlinie
        trend = calculate_trend(prices)
        if trend:
            x_vals = np.arange(len(prices))
            trend_line = trend * x_vals + prices[0]
            ax.plot(x_vals, trend_line, linestyle="-.", color="gray", label="Trend")

    ax.legend()

# ----------------- Update Trade-Liste (GUI + CSV Logging) -----------------
def update_trade_list(gui_list_widget):
    try:
        global LAST_LOGGED_TRADE
        last_entry = TRADES[-1] if TRADES else None

        if last_entry and last_entry != LAST_LOGGED_TRADE:
            with open("trade_log.csv", mode="a", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                parts = last_entry.split()
                if len(parts) >= 6 and "@" in last_entry:
                    action = parts[1]
                    volume = parts[2]
                    pair = parts[3]
                    price = parts[5]
                    reason = last_entry.split("Grund:")[-1].strip()
                    sim_label = "SIMUL" if "[SIMUL]" in last_entry else "REAL"
                    now = datetime.now()
                    writer.writerow([
                        now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), pair,
                        action.upper(), volume, price, sim_label, reason
                    ])
            LAST_LOGGED_TRADE = last_entry  # nur wenn geschrieben
        gui_list_widget.clear()
        for entry in TRADES[-20:]:
            gui_list_widget.addItem(QListWidgetItem(entry))

    except Exception as e:
        print(f"[WARN] Logging in update_trade_list fehlgeschlagen: {e}")


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
                    trend = calculate_trend(PRICE_HISTORY[pair])
                    fib0, fib382, fib618 = calculate_fibonacci_levels(PRICE_HISTORY[pair])

                    if rsi is not None and lower is not None and upper is not None:
                        if rsi < 30 and price < lower and trend > 0 and fib618 and price <= fib618:
                            last_trade = LAST_TRADE_TIME.get(pair, 0)
                            if time.time() - last_trade < TRADE_COOLDOWN_SECONDS:
                                print(f"[DEBUG] Kauf gesperrt für {pair}: Cooldown läuft.")
                                continue
                            last_buy = LAST_BUY_PRICE.get(pair)
                            if last_buy is not None and price >= last_buy * (1 - REENTRY_THRESHOLD):
                                print(f"[DEBUG] Kein Reentry-Kauf für {pair}: Preis {price:.2f} nahe letztem Kauf {last_buy:.2f}.")
                                continue
                            execute_trade(pair, "buy", amount, price,
                                f"Signal: RSI={rsi:.2f}, BB-Low={lower:.2f}, Trend={trend:.2f}, Fibo={fib618:.2f}")
                            LAST_TRADE_TIME[pair] = time.time()
                            LAST_BUY_PRICE[pair] = price

                        elif rsi > 70 and price > upper and trend < 0:
                            last_buy = LAST_BUY_PRICE.get(pair)
                            if last_buy is None:
                                continue
                            gain_eur = (price - last_buy) * amount
                            gain_pct = (price - last_buy) / last_buy * 100
                            if gain_eur < MIN_PROFIT_EUR or gain_pct < MIN_PROFIT_PCT:
                                print(f"[DEBUG] Kein Verkauf: Gewinn ({gain_eur:.2f} EUR / {gain_pct:.2f}%) zu gering.")
                                continue
                            execute_trade(pair, "sell", amount, price,
                                f"Signal: RSI={rsi:.2f}, BB-High={upper:.2f}, Trend={trend:.2f}")
                            LAST_TRADE_TIME[pair] = time.time()

                self.update_gui.emit()

                if chart_window_instance and hasattr(chart_window_instance, 'canvases'):
                    for pair in chart_window_instance.canvases:
                        chart_window_instance.update_chart(pair)

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
    else:
        try:
            nonce = str(int(time.time() * 1000))
            url_path = "/0/private/AddOrder"
            url = f"{KRAKEN_API_URL}{url_path}"
            order_data = {
                "nonce": nonce,
                "ordertype": "limit",
                "type": side,
                "volume": str(volume),
                "pair": pair,
                "price": str(price),
                "validate": False
            }
            post_data = urllib.parse.urlencode(order_data)
            message = url_path.encode() + hashlib.sha256(post_data.encode()).digest()
            signature = hmac.new(base64.b64decode(API_SECRET), message, hashlib.sha512)
            sig_b64 = base64.b64encode(signature.digest())
            headers = {
                "API-Key": API_KEY,
                "API-Sign": sig_b64.decode()
            }
            response = requests.post(url, headers=headers, data=order_data)
            response.raise_for_status()
            data = response.json()
            if data.get("error"):
                print(f"[REAL] Trade-Fehler: {data['error']}")
            else:
                msg = f"[REAL] {side.upper()} {volume} {pair} @ {price:.2f} — Grund: {reason}"
                print("[DEBUG] " + msg)
                TRADES.append(msg)
        except Exception as e:
            print(f"[ERROR] execute_trade (REAL): {e}")


# ----------------- Pair-Auswahl von Kraken -----------------
def get_available_pairs():
    try:
        url = f"{KRAKEN_API_URL}/0/public/AssetPairs"
        response = requests.get(url)
        data = response.json()
        return list(data["result"].keys())
    except Exception as e:
        print(f"[ERROR] get_available_pairs(): {e}")
        return []


# ----------------- MainWindow -----------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.api_key = ""
        self.api_secret = ""
        self.safe_asset_checkboxes = {}
        self.setWindowTitle("Kraken Trade Bot")
        self.setGeometry(100, 100, 1200, 600)
        self.bot_thread = None
        self.chart_window = None

        layout = QHBoxLayout()
        self.left_layout = QVBoxLayout()
        self.right_layout = QVBoxLayout()

        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("API Key")
        self.left_layout.addWidget(self.api_key_input)

        self.api_secret_input = QLineEdit()
        self.api_secret_input.setPlaceholderText("API Secret")
        self.left_layout.addWidget(self.api_secret_input)

        self.save_button = QPushButton("Save API Keys")
        self.save_button.clicked.connect(self.save_keys)
        self.left_layout.addWidget(self.save_button)

        self.api_test_button = QPushButton("Test API-Key")
        self.api_test_button.clicked.connect(self.check_api_keys)
        self.left_layout.addWidget(self.api_test_button)

        self.mode_button = QPushButton("Switch to Real Mode")
        self.mode_button.clicked.connect(self.toggle_mode)
        self.left_layout.addWidget(self.mode_button)

        self.portfolio_button = QPushButton("Show Portfolio")
        self.portfolio_button.clicked.connect(self.show_portfolio)
        self.left_layout.addWidget(self.portfolio_button)

        self.real_balance_button = QPushButton("Show Real Balance")
        self.real_balance_button.clicked.connect(self.get_real_balance)
        self.left_layout.addWidget(self.real_balance_button)

        self.active_pairs_button = QPushButton("Show Active Pairs")
        self.active_pairs_button.clicked.connect(self.show_active_pairs)
        self.left_layout.addWidget(self.active_pairs_button)

        self.add_pair_button = QPushButton("Add Pair")
        self.add_pair_button.clicked.connect(self.add_pair)
        self.left_layout.addWidget(self.add_pair_button)

        self.del_pair_button = QPushButton("Delete Pair")
        self.del_pair_button.clicked.connect(self.delete_pair)
        self.left_layout.addWidget(self.del_pair_button)

        self.chart_button = QPushButton("Show Charts")
        self.chart_button.clicked.connect(self.show_charts)
        self.left_layout.addWidget(self.chart_button)


        self.start_button = QPushButton("Start Bot")
        self.start_button.clicked.connect(self.start_bot)
        self.left_layout.addWidget(self.start_button)

        self.stop_button = QPushButton("Stop Bot")
        self.stop_button.clicked.connect(self.stop_bot)
        self.left_layout.addWidget(self.stop_button)

        self.license_button = QPushButton("License / Info")
        self.license_button.clicked.connect(self.show_license)
        self.left_layout.addWidget(self.license_button)

        self.status_display = QTextEdit()
        self.status_display.setReadOnly(True)
        self.left_layout.addWidget(self.status_display)

        self.trade_list = QListWidget(self)
        self.right_layout.addWidget(QLabel("Recent Trades:"))
        self.right_layout.addWidget(self.trade_list)

        layout.addLayout(self.left_layout)
        layout.addLayout(self.right_layout)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def show_active_pairs(self):
        try:
            if not TRADE_PAIRS:
                QMessageBox.information(self, "Aktive Paare", "Es sind derzeit keine aktiven Paare konfiguriert.")
                return

            lines = []
            for pair in TRADE_PAIRS:
                base = pair[:-4] if pair.endswith("EUR") else pair[:3]
                sockel = 0.0
                for asset in SAFE_BALANCES:
                    if asset.endswith(base):  # z.B. XXBT vs XBT
                        sockel = SAFE_BALANCES.get(asset, 0.0)
                        erlaubt = SAFE_ASSET_ALLOW_SELL.get(asset, False)
                        lines.append(
                            f"{pair} – Sockel: {sockel:.4f} {'(verkaufen erlaubt)' if erlaubt else '(verkauf gesperrt)'}")
                        break
                else:
                    lines.append(f"{pair} – Sockel: 0.0000 (verkauf gesperrt)")

            QMessageBox.information(self, "Aktive Handelspaare", "\n".join(lines))
        except Exception as e:
            print(f"[ERROR] show_active_pairs: {e}")
            QMessageBox.warning(self, "Fehler", f"Fehler beim Anzeigen der Paare: {e}")


    def update_interface(self):
        try:
            # Handels-Historie aktualisieren (GUI + CSV)
            update_trade_list(self.trade_list)

            # Portfolio-Tabelle aktualisieren (falls vorhanden)
            self.update_portfolio_table()

            # Charts aktualisieren
            if hasattr(self, 'chart_window'):
                print("[DEBUG] update_interface: has_attr chart_window")
                for pair in self.chart_window.canvases:
                    print(f"[DEBUG] work with {pair}")
                    self.chart_window.update_chart(pair)
                    print(f"[DEBUG] done with {pair}")


        except Exception as e:
            print(f"[ERROR] update_interface: {e}")

    def save_keys(self):
        self.api_key = self.api_key_input.text().strip()
        self.api_secret = self.api_secret_input.text().strip()

        if not self.api_key or not self.api_secret:
            QMessageBox.warning(self, "Fehler", "Bitte gültige API-Daten eingeben.")
        else:
            self.status_display.append("[INFO] API-Daten gespeichert.")
        print(f"[DEBUG] API_KEY: {repr(self.api_key)}")
        print(f"[DEBUG] API_SECRET: {repr(self.api_secret)}")

    def toggle_mode(self):
        global SIMUL, SIMUL_WALLET_VALUE, SIMUL_ASSETS, SAFE_BALANCES, SAFE_ASSET_ALLOW_SELL

        SIMUL = not SIMUL
        print("[DEBUG] toggle-mode")

        if not SIMUL:
            if not self.api_key or not self.api_secret:
                QMessageBox.warning(self, "Fehler", "Bitte API-Key und Secret zuerst speichern.")
                SIMUL = True
                return
            ok = self.test_api_credentials()
            if not ok:
                QMessageBox.critical(self, "API-Fehler", "API-Verbindung fehlgeschlagen.")
                SIMUL = True
                return

            try:
                balances = self.get_real_balance()
                SAFE_BALANCES = {}
                SAFE_ASSET_ALLOW_SELL = {}
                info_lines = []
                for asset, value in balances.items():
                    val = float(value)
                    if val > 0:
                        SAFE_BALANCES[asset] = val

                        # EUR-Assets automatisch erlauben (für Käufe)
                        if asset.endswith("EUR"):
                            continue


                        SAFE_ASSET_ALLOW_SELL[asset] = False
                        cb = QCheckBox(f"{asset}: {val:.4f} freigeben")
                        cb.stateChanged.connect(lambda state, a=asset: self.set_asset_permission(a, state))
                        self.safe_asset_checkboxes[asset] = cb
                        self.left_layout.addWidget(cb)
                        info_lines.append(f"{asset}: {val:.4f}")
                QMessageBox.information(self, "Vorhandene Assets", "\n".join(info_lines) +
                                        "\n\nNur freigegebene Assets dürfen verkauft werden. Siehe Optionen.")
            except Exception as e:
                print(f"[ERROR] Real-Balance Abfrage fehlgeschlagen: {e}")
                QMessageBox.warning(self, "Balance", f"Fehler beim Abrufen des Kontos:\n{e}")
                SIMUL = True
                return

            self.status_display.append("[REAL] Modus aktiviert. Achtung: Echter Handel möglich.")
        else:
            SIMUL_WALLET_VALUE = 1000.0
            for pair in SIMUL_ASSETS:
                SIMUL_ASSETS[pair] = 0.0
            self.status_display.append("[SIMUL] Simulationsmodus aktiviert.")

        self.mode_button.setText(f"Switch to {'Real' if SIMUL else 'Simulation'} Mode")


    def set_asset_permission(self, asset, state):
        SAFE_ASSET_ALLOW_SELL[asset] = state == Qt.CheckState.Checked

    def can_sell(self, asset, volume):
        if SIMUL:
            return True
        sockel = SAFE_BALANCES.get(asset, 0.0)
        erlaubt = SAFE_ASSET_ALLOW_SELL.get(asset, False)
        if erlaubt:
            return (SAFE_BALANCES.get(asset, 0.0) - volume) >= sockel
        return False


    def test_api_credentials(self):
        try:
            # API-Secret dekodieren
            try:
                decoded_secret = base64.b64decode(self.api_secret)
            except Exception as e:
                return False, f"API Secret ist ungültig (base64-Fehler): {e}"

            # Nonce generieren
            nonce = str(int(1000 * time.time()))

            # API-Endpunkt definieren
            url_path = "/0/private/Balance"
            url = f"{KRAKEN_API_URL}{url_path}"

            # Daten für die Anfrage vorbereiten
            post_data = {
                'nonce': nonce
            }
            postdata = urllib.parse.urlencode(post_data)
            encoded = (nonce + postdata).encode()

            # Nachricht erstellen, die signiert wird
            message = url_path.encode() + hashlib.sha256(encoded).digest()

            # HMAC-Signatur erstellen
            signature = hmac.new(decoded_secret, message, hashlib.sha512)
            sig_b64 = base64.b64encode(signature.digest())

            # HTTP-Header erstellen
            headers = {
                'API-Key': self.api_key,
                'API-Sign': sig_b64.decode()
            }

            # API-Anfrage senden
            response = requests.post(url, headers=headers, data=post_data)
            if response.status_code != 200:
                print(f"[ERROR] API-Status: {response.status_code}")
                return False, f"HTTP {response.status_code}"
            json_data = response.json()
            print("[DEBUG] API Testantwort:", json_data)
            if "result" in json_data:
                return True, "API-Key ist gültig und verbunden."
            else:
                return False, str(json_data.get("error"))
        except Exception as e:
            print(f"[ERROR] API-Test fehlgeschlagen: {e}")
            return False, str(e)


    def update_portfolio_table(self):
        # Später auf Wunsch hinzufügen oder leer lassen
        pass


    def start_bot(self):
        if not self.bot_thread:
            perform_initial_trades()  # <<< hier der neue Initialkauf
            if not self.chart_window:
                self.chart_window = ChartWindow()
            self.bot_thread = BotThread()
            self.bot_thread.update_gui.connect(self.update_interface)
            self.bot_thread.start()
            self.status_display.append("[INFO] Bot gestartet.")


    def stop_bot(self):
        if self.bot_thread:
            self.bot_thread.stop()
            self.bot_thread = None
            self.status_display.append("[INFO] Bot gestoppt.")

    def add_pair(self):
        pairs = get_available_pairs()
        pair, ok = QInputDialog.getItem(self, "Add Pair", "Kraken Trading Pair wählen:", pairs, 0, False)
        if ok and pair:
            if pair in TRADE_PAIRS:
                QMessageBox.information(self, "Hinweis", f"{pair} ist bereits aktiv.")
                return
            TRADE_PAIRS[pair] = 0.01
            PRICE_HISTORY[pair] = []
            SIMUL_ASSETS[pair] = 0.0
            self.status_display.append(f"[INFO] Paar hinzugefügt: {pair}")
            if self.chart_window:
                self.chart_window.add_chart_tab(pair)

    def delete_pair(self):
        if not TRADE_PAIRS:
            QMessageBox.information(self, "Hinweis", "Keine aktiven Paare vorhanden.")
            return
        pair, ok = QInputDialog.getItem(self, "Delete Pair", "Aktives Paar entfernen:", list(TRADE_PAIRS.keys()), 0,
                                        False)
        if ok and pair:
            TRADE_PAIRS.pop(pair, None)
            PRICE_HISTORY.pop(pair, None)
            SIMUL_ASSETS.pop(pair, None)
            self.status_display.append(f"[INFO] Paar gelöscht: {pair}")
            if self.chart_window:
                self.chart_window.remove_chart_tab(pair)

    def show_license(self):
        info = QMessageBox(self)
        info.setWindowTitle("Lizenz und Haftungshinweis")
        info.setText(
            """
            Kraken Trade Bot v1.0

            Lizenz: GPLv3
            Diese Software wird ohne Gewährleistung bereitgestellt.
            Bei Nutzung im Real-Modus haften Sie selbst für Verluste.
            Prüfen Sie alle Funktionen sorgfältig vor echtem Einsatz.
            """
        )
        info.exec()

    def show_charts(self):
        global chart_window_instance
        if not self.chart_window:
            self.chart_window = ChartWindow()
            chart_window_instance = self.chart_window
        self.chart_window.show()

    def show_portfolio(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Portfolio")

        try:
            if SIMUL:
                message = "Wallet: {:.2f} EUR\n".format(SIMUL_WALLET_VALUE)
                total = SIMUL_WALLET_VALUE
                for pair, amount in SIMUL_ASSETS.items():
                    price = fetch_price(pair)
                    value = amount * price if price else 0
                    message += "{}: {:.4f} = {:.2f} EUR\n".format(pair, amount, value)
                    total += value
                gain = total - 1000.0
                pct = (gain / 1000.0) * 100
                message += "\nGesamtwert: {:.2f} EUR\nGewinn/Verlust: {:+.2f} EUR ({:+.2f}%)".format(total, gain, pct)
            else:
                balances = self.get_real_balance()
                message = "📊 Real-Konto:\n\n"
                for asset, value in balances.items():
                    message += f"{asset}: {float(value):.4f}\n"

            dialog.setText(message)
            dialog.exec()
        except Exception as e:
            print("[ERROR] show_portfolio:", e)
            QMessageBox.warning(self, "Fehler", f"Fehler beim Berechnen des Portfolios:\n{e}")



    def check_api_keys(self):
        try:
            ok, info = self.test_api_credentials()
        except Exception as e:
            print(f"{e}")
            return

        if ok:
            QMessageBox.information(self, "API-Test", f"✅ Erfolgreich: {info}")
        else:
            QMessageBox.critical(self, "API-Test", f"❌ Fehlgeschlagen:\n{info}")

    def get_real_balance(self):
        try:
            nonce = str(int(1000 * time.time()))
            url_path = "/0/private/Balance"
            post_data = {"nonce": nonce}
            post_data_encoded = urllib.parse.urlencode(post_data)
            message = url_path.encode() + hashlib.sha256((nonce + post_data_encoded).encode()).digest()
            signature = hmac.new(base64.b64decode(self.api_secret), message, hashlib.sha512)
            sig_b64 = base64.b64encode(signature.digest())
            headers = {
                'API-Key': self.api_key,
                'API-Sign': sig_b64.decode()
            }
            url = f"{KRAKEN_API_URL}{url_path}"
            response = requests.post(url, headers=headers, data=post_data)
            if response.status_code == 200:
                return response.json().get("result", {})
            return {}
        except Exception as e:
            print(f"[ERROR] get_real_balance: {e}")
            return {}


    def place_real_order(self, pair, side, volume, price):
        try:
            decoded_secret = base64.b64decode(self.api_secret)
            nonce = str(int(1000 * time.time()))
            url_path = "/0/private/AddOrder"
            url = f"{KRAKEN_API_URL}{url_path}"
            post_data = {
                "nonce": nonce,
                "ordertype": "limit",
                "type": side,
                "volume": str(volume),
                "pair": pair,
                "price": str(price)
            }
            postdata = urllib.parse.urlencode(post_data)
            encoded = (nonce + postdata).encode()
            message = url_path.encode() + hashlib.sha256(encoded).digest()
            signature = hmac.new(decoded_secret, message, hashlib.sha512)
            sig_b64 = base64.b64encode(signature.digest())
            headers = {
                "API-Key": self.api_key,
                "API-Sign": sig_b64.decode()
            }
            response = requests.post(url, headers=headers, data=post_data)
            print("[DEBUG] Real Order Antwort:", response.json())
            return response.json()
        except Exception as e:
            print(f"[ERROR] Real Order fehlgeschlagen: {e}")
            return None


    # ----------------- ChartWindow -----------------
class ChartWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Live Charts")
        self.tabs = QTabWidget()
        layout = QVBoxLayout()
        layout.addWidget(self.tabs)
        self.setLayout(layout)
        self.timers = {}
        self.canvases = {}

        for pair in TRADE_PAIRS:
            self.add_chart_tab(pair)

    def remove_chart_tab(self, pair):
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == pair:
                self.tabs.removeTab(i)
                break
        self.timers.pop(pair, None)
        self.canvases.pop(pair, None)

    def add_chart_tab(self, pair):
        canvas = FigureCanvas(Figure(figsize=(8, 4)))
        ax = canvas.figure.add_subplot(111)
        self.canvases[pair] = (canvas, ax)

        widget = QWidget()
        tab_layout = QVBoxLayout()
        tab_layout.addWidget(canvas)
        widget.setLayout(tab_layout)

        self.tabs.addTab(widget, pair)

        timer = QTimer()
        timer.timeout.connect(lambda p=pair: self.plot(p))
        timer.start(5000)
        self.timers[pair] = timer


    def plot(self, pair):
        canvas, ax = self.canvases[pair]
        ax.clear()
        prices = PRICE_HISTORY.get(pair, [])
        if not prices:
            return
        ax.plot(prices[-100:], label="Price", color="blue")

        # Trendpfeil (einfacher linearer Trend)
        if len(prices) >= 10:
            x = np.arange(len(prices[-10:]))
            y = np.array(prices[-10:])
            slope, _ = np.polyfit(x, y, 1)
            trend = "↑" if slope > 0 else "↓"
            ax.set_title(f"{pair} – letzte 100 Preise   Trend: {trend}")
        else:
            ax.set_title(f"{pair} – letzte 100 Preise")

        # Zusätzliche Linien
        current_price = prices[-1]
        next_buy = current_price * (1 - REENTRY_THRESHOLD)
        next_sell = current_price * (1 + TAKE_PROFIT_DYNAMIC)
        stop_loss = current_price * (1 - STOP_LOSS_DYNAMIC)
        ax.axhline(y=next_buy, color="green", linestyle="--", label="Next Buy")
        ax.axhline(y=next_sell, color="red", linestyle="--", label="Next Sell")
        ax.axhline(y=stop_loss, color="orange", linestyle=":", label="Stop-Loss")

        # Fibonacci-Linien
        high = max(prices)
        low = min(prices)
        fib_0 = high
        fib_382 = high - (high - low) * 0.382
        fib_618 = high - (high - low) * 0.618
        ax.axhline(y=fib_0, color="purple", linestyle="--", label="Fibo 0.0")
        ax.axhline(y=fib_382, color="purple", linestyle="--", label="Fibo 38.2")
        ax.axhline(y=fib_618, color="purple", linestyle="--", label="Fibo 61.8")

        ax.legend()
        canvas.draw()

    def update_chart(self, pair):
        try:
            if pair not in self.canvases:
                return
            canvas, ax = self.canvases[pair]
            self.plot(pair)
        except Exception as e:
            print(f"[ERROR] Chart update failed for {pair}: {e}")




#######################
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
