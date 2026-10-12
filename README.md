# PrlCalc
Automatic analysis tool with integration for pearlhash wallets with support for saladcloud gpu rental.

Live dashboard + calculator: profit/break-even at live PRL price, per-GPU hashrate, pool/network shares, container group start/stop, and a what-if calculator for SaladCloud GPU prices.

## Run

- Web: `python pearlcalc.py` → http://localhost:8000 (stdlib only)
- Desktop: `python app.py` (needs `pip install pywebview`) or build a single exe with `build-desktop.ps1` (needs `pip install pyinstaller`)
- Tests: `python test_pearlcalc.py`
- UI screenshots (dev): `npm i` then `npm run shot`

## Configure

Open the **Settings** tab in the app: wallet address, Salad org/project, Salad API key, pool fee.
Values are stored in `config.json` next to the script/exe — kept local and gitignored, never uploaded. `config.example.json` shows the shape.

## Data

- pearlhash.xyz public API — wallet, price, chain stats
- SaladCloud public API — GPU classes, container groups, instances

## License

MIT — see LICENSE

