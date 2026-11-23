# Bot Telegram de signaux de trading (privé, multi-fichiers)

Bot Telegram qui analyse les marchés disponibles sur HFM MetaTrader 5 (et les sources de secours pour les actifs non proposés par HFM) et t'envoie un signal **ACHAT / VENTE** avec
entrée, Take Profit, Stop Loss, ratio risque/rendement et score de confiance —
**plus une image du graphique** montrant comment il a raisonné (bougies, EMA,
Bollinger, RSI, MACD, niveaux tracés).

![exemple d'analyse](exemple_analyse.png)

---

## 1. Structure du projet (multi-fichiers)

```
telegram-trading-bot/
├── bot.py                  # POINT D'ENTRÉE : commandes, boutons, envoi Telegram
├── config/
│   └── settings.py         # toute la configuration (.env + constantes)
├── data/
│   ├── markets.py          # catalogue statique + catalogue HFM découvert à l'exécution
│   ├── feed.py             # routage des sources, bougies clôturées et cache
│   └── mt5_provider.py     # lecture des symboles, ticks et bougies HFM via MT5
├── core/                   # le cerveau : tout le calcul technique
│   ├── indicators.py       # EMA, RSI, MACD, ATR, Bollinger, Stochastique
│   ├── patterns.py         # chandeliers japonais (pin bar, englobante, ...)
│   ├── signal_engine.py    # combine tout -> verdict ACHAT/VENTE/ATTENDRE
│   └── risk.py             # TP/SL, ratio R:R, taille de position
├── services/
│   ├── mtf.py              # contrôle multi-timeframe (5m/15m/1h...)
│   └── scanner.py          # scan automatique de l’univers HFM
├── outputs/
│   ├── chart.py            # génération de l'image d'analyse
│   └── messages.py         # textes des messages Telegram
├── storage/
│   └── history.py          # historique des signaux + suivi TP/SL + watchlist
├── utils/
│   ├── logger.py           # logs console + logs/bot.log (rotation)
│   └── helpers.py          # heure locale, formats
├── tests/                  # tests unitaires (sans réseau)
│   ├── test_indicators.py
│   ├── test_patterns.py
│   └── test_signal_engine.py
├── deploy/                 # mise en production
│   ├── Dockerfile + docker-compose.yml
│   ├── bot.service         # service systemd
│   ├── run.sh              # lancement en un script
│   └── GUIDE-GITHUB-SERVEUR.md  # GitHub + serveur pour tourner 24h/24
├── data_store/             # (créé au runtime) signaux.json, watchlist.json
├── logs/                   # (créé au runtime) bot.log
├── requirements.txt
├── .env.example            # modèle de configuration
├── .gitignore              # protège .env, logs et données (pour GitHub)
├── README.md
└── exemple_analyse.png
```

Chaque dossier a un rôle clair : tu peux ajouter un indicateur dans `core/`,
un actif dans `data/markets.py` ou une commande dans `bot.py` sans tout casser.

---

## 2. Commandes du bot

| Commande | Effet |
|---|---|
| `/start` | menu de démarrage avec boutons (Crypto / Forex / OTC) |
| `/analyse BTC 5m` | analyse complète immédiate + image |
| `/analyse EUR/USD 15m` | idem forex ; `/analyse XAU 1h` pour l'or |
| `/prix BTC` | prix en direct + variation 24h |
| `/marches` | marchés connus et symboles HFM découverts |
| `/hfm` / `/hfm refresh` | état MT5, nombre d'instruments et actualisation du catalogue HFM |
| `/scan on` / `/scan off` | scan automatique de tous les instruments négociables découverts sur HFM MT5 |
| `/watch add ETH 15m` | modifier la watchlist historique (utilisée lorsque le scan univers HFM est désactivé) |
| `/watch remove ETH 15m` | retirer ; `/watch clear` tout vider |
| `/stats` | résultats des signaux passés (TP touché vs SL touché) |
| `/id` | ton ID Telegram |
| `/help` | aide détaillée |

À chaque analyse tu reçois **2 choses** :
1. une **image** : bougies, EMA 9/21, bandes de Bollinger, lignes Entrée/TP/SL,
   marqueur du signal, panneaux RSI et MACD ;
2. un **message détaillé** : la trace écrite — chaque module avec ses valeurs,
   les figures de chandeliers détectées, le verdict multi-timeframe, le
   ratio risque/rendement et la suggestion de taille de position.

---

## 3. Installation pas à pas

Prérequis : Python 3.10+ (3.12 conseillé).

### Étape 1 — dépendances
```bash
pip install -r requirements.txt
```

### Étape 2 — token du bot
Sur Telegram : **@BotFather** -> `/newbot` (ou `/token`) -> copie la chaîne
`1234567890:AAE...` (c'est le **token**).

### Étape 3 — ton ID Telegram
Cherche **@userinfobot**, envoie n'importe quoi : il donne ton **Id** numérique.

### Étape 4 — configuration
```bash
cp .env.example .env
```
Puis remplis dans `.env` :
```
BOT_TOKEN=ton_token
ADMIN_ID=ton_id
```
(Toutes les autres lignes ont des valeurs par défaut raisonnables.)

### Étape 5 — lancement
```bash
python bot.py
```
Le bot t'envoie "Bot démarré !". Laisse le terminal ouvert tant que tu veux
que le bot tourne (voir section 7 pour le faire tourner en continu).

### Étape 6 — vérifier les tests (optionnel mais conseillé)
```bash
pytest -q
```

---

## 4. Comment le bot décide (système de points)

Chaque module donne des points au scénario haussier ou baissier — maximum 12
par côté :

| Module | Points max | Ce qu'il regarde |
|---|---|---|
| Tendance EMA 9/21 | 2 | prix au-dessus/en-dessous + EMA9 vs EMA21 |
| Croisement EMA récent | 2 | retournement sur les dernières bougies |
| RSI(14) | 2 | survente < 30 (achat), surachat > 70 (vente), biais 45/55 |
| MACD(12,26,9) | 2 | ligne vs signal + direction de l'histogramme |
| Bandes de Bollinger(20,2) | 1 | prix collé à une bande extrême |
| Stochastique(14,3) | 1 | croisements en zones basses (< 25) / hautes (> 75) |
| Chandeliers japonais | 2 | pin bar, englobante, marubozu, 3 soldats/corbeaux |

Décision :
- **Direction** = côté dominant ; **Confiance %** = sa part du total
- Signal confirmé seulement si : confiance >= 62% **et** écart >= 2 points
  **et** côté dominant >= 5 points (réglables dans `.env`)
- **TP / SL** calibrés sur l'ATR : TP = entrée + 1,5 x ATR,
  SL = entrée - 1,0 x ATR -> ratio risque/rendement 1:1.5 par défaut
- **Multi-timeframe** : le bot revérifie sur les timeframes supérieurs
  (ex : 5m -> 15m + 1h) et te dit si le signal est renforcé ou fragile
- **Suivi automatique** : chaque signal confirmé est enregistré ; à chaque
  scan et /stats, le bot vérifie sur les cours réels si le TP ou le SL a été
  touché en premier et calcule ton taux de réussite par actif
  (les deux touchés dans la même bougie = compté perdu, hypothèse conservatrice)

---

## 5. Marchés et sources de données

### HFM MetaTrader 5 (source prioritaire)

Sur Windows, le bot se connecte au terminal MetaTrader 5 déjà ouvert et authentifié. Il ne demande ni ne stocke le mot de passe HFM. Au démarrage et lors du rafraîchissement du catalogue, il lit les symboles disponibles dans le terminal avec `symbols_get()`, classe les instruments selon les groupes et les métadonnées MT5, puis analyse les symboles avec leurs noms exacts de broker.

Les symboles HFM découverts sont toujours analysés depuis HFM MT5. Si les bougies HFM d'un symbole sont indisponibles, le bot ne bascule pas silencieusement vers Yahoo Finance, Deriv ou Binance pour ce même symbole : il signale une erreur. Cela évite de te donner une entrée calculée sur une cotation différente de celle de ton compte.

Le scanner `/scan on` surveille **uniquement tous les instruments négociables découverts dans le catalogue HFM**. Il utilise le timeframe par défaut du marché (15m pour Forex/crypto ; 1h pour les autres catégories), puis exécute les contrôles multi-timeframe déjà présents lorsqu'un signal apparaît. Les signaux ACHAT/VENTE confirmés sont envoyés selon le format historique du bot : image du graphique puis message détaillé. Les signaux ATTENDRE ne deviennent pas des alertes, comme dans le fonctionnement d'origine. La watchlist historique (`/watch`) est conservée et redevient le périmètre du scan si le mode `SCAN_ALL_HFM_SYMBOLS` est désactivé.

### Sources complémentaires pour les analyses manuelles

| Situation | Source utilisée |
|---|---|
| Instrument découvert dans HFM MT5 | **HFM MT5 uniquement** |
| Crypto statique absente de HFM, analysée manuellement | Deriv, puis Binance, puis Yahoo Finance selon le catalogue |
| Autre actif statique absent de HFM, analysé manuellement | Deriv, puis Yahoo Finance selon le catalogue |

Les bougies en formation sont écartées avant l'analyse. L'alerte conserve le nom exact du symbole et la source (par exemple `HFM MT5 (EURUSD)`). Les images, indicateurs, score, seuils, TP, SL, messages Telegram, cooldown et suivi TP/SL restent ceux du projet d'origine.

### Installation et connexion HFM

1. Installe MetaTrader 5 pour Windows et connecte ton compte HFM Premium dans le terminal de bureau.
2. Depuis le dossier du projet, installe les dépendances : `python -m pip install -r requirements.txt`. Sur Windows x64, ce fichier installe aussi le connecteur officiel `MetaTrader5`.
3. Lance le bot avec `python bot.py`.
4. Dans Telegram, tape `/hfm`. Le bot doit afficher la connexion et le nombre d'instruments détectés. Utilise `/hfm refresh` après avoir ajouté de nouveaux symboles dans MT5.
5. Tape `/marches` pour voir les catégories et une partie des symboles, puis `/scan on` pour démarrer le scan complet. Les symboles non affichés dans la liste restent accessibles avec `/analyse SYMBOLE 15m`.

`MT5_PATH` et `MT5_SERVER` sont facultatifs dans `.env`. Ne mets jamais ton mot de passe HFM, ton token Telegram ou tes identifiants dans le code partagé. Le terminal doit rester ouvert et connecté pendant le scan.

**Hébergement :** le workflow GitHub Actions fourni utilise un runner Ubuntu. Il ne peut pas s'attacher à ton terminal HFM local Windows. Pour les cours HFM directs, lance le bot sur ton PC Windows ou sur un VPS Windows sur lequel MT5 est installé et connecté. Le workflow GitHub Actions reste un mode historique utilisant les sources non-MT5 et une watchlist limitée.

**Sécurité d'exécution :** cette intégration ne passe aucun ordre. Elle utilise la connexion MT5 uniquement pour lire l'état du terminal, la liste des symboles, les ticks et les bougies. Le bot conserve le rôle de générateur de signaux Telegram, et tu continues à placer les transactions manuellement.

---

## 6. Réglages (fichier `.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `BOT_TOKEN` / `ADMIN_ID` | (obligatoires) | token du bot / ton ID |
| `ALLOWED_IDS` | vide | autres IDs autorisés |
| `SIGNAL_MIN_CONFIDENCE` | 62 | confiance minimale pour un signal |
| `SIGNAL_MIN_POINTS` | 5 | points minimum (sur 12) |
| `SIGNAL_MIN_MARGIN` | 2 | écart minimum entre les deux côtés |
| `SCAN_INTERVAL_MIN` | 5 | intervalle du scan automatique |
| `COOLDOWN_MIN` | 15 | délai avant de renvoyer le même signal |
| `AUTO_SCAN` | 0 | démarre le scan automatiquement au lancement (`1` pour l'activer) |
| `WATCHLIST` | BTC 5m, EUR/USD 5m, BTC OTC 5m | watchlist initiale |
| `BALANCE` / `RISK_PCT` | 0 / 1.0 | balance fictive et % de risque -> taille de position |
| `TP_ATR` / `SL_ATR` | 1.5 / 1.0 | calibration TP/SL sur l'ATR |
| `TZ_NAME` | Africa/Lubumbashi | fuseau affiché |
| `NB_CANDLES_CHART` | 80 | bougies dessinées sur l'image |
| `FEED_CACHE_TTL` | 5 | cache court des bougies (secondes) |
| `DERIV_APP_ID` | 1089 | ID d'application public utilisé par le WebSocket Deriv |
| `MT5_ENABLED` | 1 | active l'accès au terminal MetaTrader 5 sous Windows |
| `MT5_PATH` | vide | chemin facultatif vers terminal64.exe |
| `MT5_SERVER` | vide | contrôle facultatif du serveur connecté |
| `MT5_SYMBOL_REFRESH_MIN` | 30 | fréquence d'actualisation du catalogue HFM |
| `SCAN_ALL_HFM_SYMBOLS` | 1 | surveille tous les symboles négociables découverts chez HFM |

Les symboles HFM sont découverts automatiquement depuis MT5 ; le catalogue statique reste dans `data/markets.py`.

---

## 7. Faire tourner le bot en continu

Options d'exécution :

**a) Sessions GitHub Actions** (mode expérimental, disponibilité non garantie) :

Le workflow `.github/workflows/bot-24h.yml` démarre le bot en sessions
programmées et sauvegarde l'historique. Ajoute les secrets `BOT_TOKEN` et
`ADMIN_ID` dans **Settings > Secrets and variables > Actions**, puis teste-le
depuis **Actions > Bot par sessions > Run workflow**.

Ce n'est pas un hébergement 24h/24 garanti : chaque session s'arrête après
environ 5 h 35, et le bot ne répond pas pendant les interruptions. GitHub peut
aussi retarder un lancement. La cron `3 */6 * * *` est en UTC (02:03, 08:03,
14:03 et 20:03 en UTC+2). Utilise ce mode seulement pour un dépôt public et
vérifie les limites et règles d'utilisation GitHub Actions. Pour une présence
interactive fiable en continu, utilise plutôt un VPS ou un serveur toujours actif.
Voir le [guide GitHub Actions](./deploy/GUIDE-GITHUB-ACTIONS.md) pour la mise en place.

**b) Script local** (Linux/macOS) :
```bash
deploy/run.sh          # crée le venv, installe, lance
```

**c) Docker** :
```bash
cd deploy && docker compose up -d --build
docker compose logs -f bot
```

**d) systemd** (serveur Linux) :
```bash
sudo cp deploy/bot.service /etc/systemd/system/   # adapter User et chemin
sudo systemctl enable --now trading-bot
journalctl -u trading-bot -f
```

Les données (`data_store/`) et les logs (`logs/`) survivent aux redémarrages.
Pour l'hébergement 24h/24 via GitHub (créer le dépôt, le pousser, déployer sur
un serveur gratuit ou un VPS), suis le pas-à-pas complet :
**`deploy/GUIDE-GITHUB-SERVEUR.md`**.

---

## 8. Dépannage

| Symptôme | Solution |
|---|---|
| `BOT_TOKEN manquant` | crée le `.env` (copie `.env.example`) |
| `ADMIN_ID manquant` | récupère ton ID via @userinfobot puis remplis le `.env` |
| "Ce bot est privé" | ton ID n'est pas dans `ADMIN_ID`/`ALLOWED_IDS` |
| "Impossible d'analyser... marché fermé" | forex fermé le week-end ou API indisponible : réessaie plus tard |
| Binance HTTP 418 | ban temporaire de Binance (trop de requêtes) : le bot bascule sur Yahoo, attends quelques minutes |
| Le bot ne répond plus | terminal fermé ou coupure réseau : relance (ou systemd/docker qui relance tout seul) |

---

## 9. Avertissement important

Ce bot est un **outil éducatif d'analyse technique**. Les signaux sont le
résultat de calculs sur les cours passés : **aucun signal n'est garanti**, le
marché peut se retourner instantanément, et le trading (marge, options
binaires) comporte un **risque de perte totale**. Ne trade jamais de l'argent
dont tu as besoin, et ne considère pas ce bot comme un conseil financier.
Vérifie toujours les niveaux sur ta plateforme avant d'agir.
