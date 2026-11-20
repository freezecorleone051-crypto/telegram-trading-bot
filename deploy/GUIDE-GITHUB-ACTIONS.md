# Bot Telegram avec GitHub Actions

Ce mode lance le bot complet pendant des sessions planifiées. Il peut répondre
aux commandes Telegram pendant une session, mais ce n'est pas un hébergement
continu garanti : les sessions s'arrêtent et GitHub peut retarder ou ne pas
lancer un événement planifié à l'heure prévue.

## Préparer le dépôt

1. Utilise un dépôt public si tu veux éviter la consommation de minutes facturées
   pour un dépôt privé. Le code du dépôt public sera visible ; les secrets
   `BOT_TOKEN` et `ADMIN_ID` restent chiffrés par GitHub.
2. Vérifie que `.env`, `.venv`, `logs/` et les données locales ne sont pas suivis
   par Git. Ne téléverse jamais le fichier `.env` ni le token du bot.
3. Dans **Settings > Secrets and variables > Actions**, ajoute les secrets :
   - `BOT_TOKEN` : token créé avec @BotFather ;
   - `ADMIN_ID` : identifiant numérique de ton compte Telegram.
4. Le workflow doit être sur la branche par défaut. Lance un test via
   **Actions > Bot par sessions > Run workflow**.

## Fonctionnement

Le workflow `.github/workflows/bot-24h.yml` planifie un lancement à 03 minutes
des heures 00, 06, 12 et 18 UTC, soit 02:03, 08:03, 14:03 et 20:03 en UTC+2.
Chaque session démarre le bot complet, active le scan automatique toutes les
5 minutes et s'arrête après environ 5 h 35. L'historique est ensuite commité
dans `data_store/`.

Entre les sessions, le bot ne répond pas. Les retards de planification peuvent
allonger les interruptions ; cette méthode ne remplace pas un service ou un VPS
toujours actif. Le dépôt public permet les minutes standard gratuites selon les
conditions actuelles de GitHub, mais ne garantit ni la disponibilité ni le
maintien de ces conditions.

Pour arrêter le mode, désactive le workflow dans l'onglet **Actions** ou retire
le fichier `.github/workflows/bot-24h.yml`.
