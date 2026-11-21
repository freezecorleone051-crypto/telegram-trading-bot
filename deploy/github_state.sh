#!/usr/bin/env bash
# ==================================================================
# Historique du bot : sauvegarde / restauration entre les sessions.
#   bash deploy/github_state.sh restore   -> au DÉBUT de la session
#   bash deploy/github_state.sh save      -> à la FIN de la session
# L'historique (data_store/) vit sur la branche dédiée "bot-state".
# Le dossier est ignoré par Git sur main : on le force uniquement ici.
# ==================================================================
set -euo pipefail

MODE="${1:-}"
BRANCH="${BOT_STATE_BRANCH:-bot-state}"
STATE_DIR="data_store"

if [ "$MODE" != "restore" ] && [ "$MODE" != "save" ]; then
  echo "Usage : bash deploy/github_state.sh restore|save" >&2
  exit 1
fi

git config user.name  "trading-bot-actions"
git config user.email "actions@github.com"

if [ "$MODE" = "restore" ]; then
  if git fetch --quiet origin "$BRANCH" 2>/dev/null; then
    if git cat-file -e "FETCH_HEAD:$STATE_DIR" 2>/dev/null; then
      rm -rf "$STATE_DIR"
      git archive "FETCH_HEAD" "$STATE_DIR" | tar -x
      echo "Historique restauré depuis la branche '$BRANCH'."
    else
      echo "La branche '$BRANCH' n'a pas de dossier $STATE_DIR — rien à restaurer."
    fi
  else
    echo "Première exécution : pas encore de branche '$BRANCH', historique vide."
  fi
  exit 0
fi

if [ ! -d "$STATE_DIR" ] || [ -z "$(ls -A "$STATE_DIR" 2>/dev/null)" ]; then
  echo "Aucune donnée à sauvegarder (dossier $STATE_DIR vide ou absent)."
  exit 0
fi

git fetch --quiet origin "$BRANCH" 2>/dev/null || true
HAS_BASE=0
if git rev-parse --verify -q "origin/$BRANCH" >/dev/null 2>&1; then
  HAS_BASE=1
fi

TMP="$(mktemp -d)"
cleanup() {
  git worktree remove --force "$TMP/state" >/dev/null 2>&1 || true
  git worktree prune >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

if [ "$HAS_BASE" = 1 ]; then
  git worktree add --quiet --detach "$TMP/state" "origin/$BRANCH"
  mkdir -p "$TMP/state/$STATE_DIR"
else
  git worktree add --quiet --detach "$TMP/state" "HEAD"
  git -C "$TMP/state" rm -rq --ignore-unmatch . 2>/dev/null || true
  mkdir -p "$TMP/state/$STATE_DIR"
fi

cp -a "$STATE_DIR"/. "$TMP/state/$STATE_DIR/"
git -C "$TMP/state" add -f "$STATE_DIR"

if git -C "$TMP/state" diff --cached --quiet; then
  echo "Historique inchangé, rien à sauvegarder."
  exit 0
fi

if [ "$HAS_BASE" = 1 ]; then
  git -C "$TMP/state" commit -q -m "Historique du bot [skip ci]"
  git -C "$TMP/state" push origin "HEAD:$BRANCH"
else
  TREE="$(git -C "$TMP/state" write-tree)"
  COMMIT="$(echo "Historique du bot (première sauvegarde)" | git -C "$TMP/state" commit-tree "$TREE")"
  git push origin "$COMMIT:refs/heads/$BRANCH"
fi
echo "Historique sauvegardé sur la branche '$BRANCH'."
