# Guide — GitHub + serveur pour un bot 24h/24

## Le principe en une image

```
TON PC (code)  --git push-->  GITHUB (placard en ligne)  --git clone-->  SERVEUR 24h/24
                                                                              |
                                                                        Bot Telegram
                                                                              |
                                                                        Ton téléphone
```

- **GitHub** ne fait pas tourner le bot : c'est un **placard à code** en ligne.
- Ce qui fait tourner le bot 24h/24, c'est une **machine allumée en permanence**
  (un "serveur"). GitHub sert juste à transporter ton code vers ce serveur,
  proprement et en une commande.

---

## 1. Choisir la machine qui tournera 24h/24

| Option | Prix | Fiabilité | Pour qui |
|---|---|---|---|
| Ton PC laissé allumé | gratuit | moyenne (coupures, veille) | tester quelques jours |
| **Oracle Cloud "Always Free"** | **gratuit à vie** | bonne | budget zéro (carte bancaire demandée juste pour vérifier ton identité, pas de prélèvement) |
| VPS pas cher (Hetzner, Hostinger, Contabo) | 3 à 5 €/mois | excellente | la solution sérieuse, celle des pros |
| Railway / Render (se connectent à GitHub) | ~5 $/mois après essai | bonne | le plus simple, mais payant |

> Astuce : certains bricolent avec GitHub Actions pour faire tourner le bot
> par tranches de 6 h. Ça casse tout le temps, les données sautent, et ça peut
> faire bannir le compte GitHub. À éviter.

Recommandation : **Oracle Cloud Always Free** (gratuit) ou un **VPS à 4 €/mois**
Ubuntu 22.04/24.04 chez Hetzner ou Hostinger. Les deux marchent pareil ensuite.

---

## 2. Mettre le code sur GitHub (une seule fois)

### a) Crée un compte sur github.com puis un dépôt
- Bouton **New repository** -> nom : `telegram-trading-bot`
- Choisis **Private** (important : ton code de trading reste pour toi)
- NE coche PAS "Add README" (on va déjà en avoir un)
- Clique **Create repository**

### b) Sur ton PC, dans le dossier du projet
```bash
cd telegram-trading-bot
git init
git add .
git commit -m "Premiere version du bot de signaux"
git branch -M main
git remote add origin https://github.com/TON_PSEUDO/telegram-trading-bot.git
git push -u origin main
```
(GitHub te demandera ton pseudo + un "Personal Access Token" : crée-le sur
GitHub -> Settings -> Developer settings -> Tokens -> Generate new token,
coche "repo", copie-le comme mot de passe.)

### c) Vérifie la sécurité
Le fichier `.gitignore` du projet protège déjà le `.env`, les logs et les
données. Vérifie sur la page GitHub du dépôt : **le fichier .env ne doit PAS
apparaître**. Si un jour tu pousses ton token par accident :
BotFather -> `/revoke` -> nouveau token, et change le `.env`.

---

## 3. Préparer le serveur (Ubuntu)

Connexion au serveur (Oracle Cloud ou VPS te donne une IP + une clé SSH) :
```bash
ssh ubuntu@IP_DU_SERVEUR
```

Installer ce qu'il faut :
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git
```

---

## 4. Récupérer le code et configurer

```bash
cd /opt
sudo mkdir trading-bot && sudo chown $USER trading-bot
git clone https://github.com/TON_PSEUDO/telegram-trading-bot.git trading-bot
cd trading-bot
```

Créer le `.env` SUR LE SERVEUR (il n'est pas sur GitHub, et c'est voulu) :
```bash
cp .env.example .env
nano .env        # remplis BOT_TOKEN et ADMIN_ID, Ctrl+X pour sauver
```

---

## 5. Lancer le bot en continu

### Option simple : le script fourni (test rapide)
```bash
chmod +x deploy/run.sh
deploy/run.sh
```

### Option sérieuse : service systemd (redémarre tout seul après une coupure)
```bash
sudo cp deploy/bot.service /etc/systemd/system/
sudo nano /etc/systemd/system/bot.service
#   -> remplace User=ton_utilisateur par User=ubuntu
#   -> WorkingDirectory et ExecStart pointent déjà vers /opt/trading-bot
sudo systemctl daemon-reload
sudo systemctl enable --now trading-bot
journalctl -u trading-bot -f        # voir les logs en direct (Ctrl+C pour sortir)
```

Le bot tourne maintenant **24h/24**, même si tu fermes tout et que ton PC
s'éteint. Il redémarre tout seul après une panne de réseau ou de serveur
(`Restart=always`).

---

## 6. Le quotidien : améliorer le bot puis mettre à jour le serveur

Sur ton PC (tu modifies le code, par exemple un nouvel actif dans
`data/markets.py`) :
```bash
git add .
git commit -m "Ajout de LINK/USDT et MATIC/USDT"
git push
```

Sur le serveur :
```bash
cd /opt/trading-bot
git pull
sudo systemctl restart trading-bot
```

C'est ça le vrai rôle de GitHub dans ton projet : **transporter chaque
amélioration vers le serveur en 2 commandes**, avec l'historique de tout ce
que tu as changé.

---

## 7. Récap sécurité

| Règle | Pourquoi |
|---|---|
| Dépôt **Private** | ton code de trading t'appartient |
| Jamais de `.env` sur GitHub | le token du bot = les clés du bot ; des robots scannent GitHub pour voler les tokens |
| `.gitignore` actif (déjà fourni) | empêche d'envoyer `.env`, logs, données par accident |
| Token révoqué si exposé | BotFather -> `/revoke`, puis nouveau token dans le `.env` du serveur |
| Dépôt privé = jamais de signaux publics | le bot reste réservé à ton `ADMIN_ID` |
