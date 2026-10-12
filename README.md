# PrlCalc

Automatic analysis tool with integration for pearlhash wallets with support for saladcloud gpu rental.

I wrote this to watch my own mining setup without having five browser tabs open. It reads my worker hashrate and the live PRL price from pearlhash, reads GPU prices and container state from the SaladCloud API, and boils all of it down to the numbers I actually care about: what each card earns per day, what I'm paying per terahash, the price PRL needs to hold for me to break even, and which priority tier would earn the most for the hardware I'm renting.

The calculator tab works standalone too. Pick a GPU class and a priority tier, type in a hashrate if you don't trust the measured one, and it shows profit per hour and per day. The groups table lets you start and stop container groups without opening the Salad portal.

Everything refreshes itself every 30 seconds, and there's a button to force it if you're impatient.

## Running it

The web version needs nothing but Python:

    python pearlcalc.py

then open http://localhost:8000.

If you'd rather skip Python entirely, the Releases page has a prebuilt pearlcalc.exe. There's also `python app.py` for a native window (install pywebview first), and `build-desktop.ps1` if you want to build the exe yourself (install pyinstaller).

Tests: `python test_pearlcalc.py`. The screenshot script is a development tool and needs playwright (`npm i`).

## Settings

On first run, open the Settings tab and fill in your Pearl wallet address, Salad org, project name, API key and the pool fee. They're saved to config.json next to the script or exe, which stays on your machine and is gitignored. The only things that leave your computer are the API requests themselves. If you'd rather edit a file, config.example.json shows the format.

## Where the data comes from

pearlhash.xyz exposes a public API for wallet stats, the live price, block reward, difficulty and network hashrate. SaladCloud exposes a public API for GPU class pricing, container groups and their instances. No scraping needed, and nothing sitting between you and the numbers.

## License

MIT, see LICENSE.
