# tradebot
a cryptocurrency trading bot for Kraken  

made by @frickl with help from ChatGPT

If you find this project helpful you might 
want to donate a coffee or more ..?

LTC : LVwX9iHQaVFws6Y1AwKpkh7dxVY7vZoPGt

BTC : 3JjFKm4WYFJTjpGJgRfFFpWRjkSAhMFdtx
 
Thank you!

################################

Installation:

You need python3 with some extra modules.
use : pip install <module>

* PyQt6
* matplotlib
* xcffib
* requests

I recommend a virtual environment (venv).
Easy setup can be done by using a tool
like PyCharm (Community Edition) or vscode

################################

Usage:

This bot runs in a Simulation Mode when started.
It assumes a starting fund of 1000 € and trades with
pairs SOL - EUR and ETH - EUR
More pairs can be added or deleted.
Chart information will be displayed for all
pairs ín use.
After adding API key information real trade is possible;
a toggle button switches to real mode.

Trade decisions are made by:

* RSI
* trend (linear regession)
* stop-loss (dynamic)
* Bollinger bands

The bot does logging a csv file for usage for taxes i.e.
(Remember that you have to tax every win-deal)

To add the API keys you have to login at Kraken
and move to https://pro.kraken.com/app/settings/api
Here you can generate your own API key.
Do not tell this key to anybody!

Happy trading!
