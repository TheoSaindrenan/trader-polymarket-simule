#!/usr/bin/env bash
# Installe le trader simulé comme service permanent sur un VPS Debian/Ubuntu
# (testé pour une e2-micro Google Cloud). Usage, sur le VPS :
#   curl -fsSL https://raw.githubusercontent.com/TheoSaindrenan/trader-polymarket-simule/main/installer_vps.sh | bash
set -euo pipefail

DEPOT="https://github.com/TheoSaindrenan/trader-polymarket-simule"
DOSSIER="$HOME/trader"
CONF="$HOME/.trader.env"

echo "== Paquets"
sudo apt-get update -qq
sudo apt-get install -y -qq git python3 python3-venv >/dev/null

echo "== Code"
if [ -d "$DOSSIER/.git" ]; then git -C "$DOSSIER" pull -q; else git clone -q "$DEPOT" "$DOSSIER"; fi
python3 -m venv "$DOSSIER/.venv"
"$DOSSIER/.venv/bin/pip" install -q -r "$DOSSIER/requirements.txt"

# Le journal et l'état du VPS restent sur la machine (pas de push vers GitHub).
mkdir -p "$HOME/trader-donnees"

echo "== Clés"
if [ ! -f "$CONF" ]; then
  read -r -s -p "ANTHROPIC_API_KEY : " CLE_A </dev/tty; echo
  read -r -s -p "X_BEARER_TOKEN (Entrée pour ignorer) : " CLE_X </dev/tty; echo
  umask 077
  cat > "$CONF" <<EOF
ANTHROPIC_API_KEY=$CLE_A
X_BEARER_TOKEN=$CLE_X
X_BUDGET_JOUR=1
DOSSIER_TRADER=$HOME/trader-donnees
EOF
  echo "Clés enregistrées dans $CONF (lisible par toi seul)."
else
  echo "$CONF existe déjà, je le garde."
fi

echo "== Service"
sudo tee /etc/systemd/system/trader-simule.service >/dev/null <<EOF
[Unit]
Description=Trader simulé Polymarket (aucun pari réel)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$DOSSIER
EnvironmentFile=$CONF
ExecStart=$DOSSIER/.venv/bin/python3 -u $DOSSIER/trader_simule.py
# Redémarre en cas de plantage, mais pas après un arrêt volontaire (-40 % ou cagnotte à 0).
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now trader-simule

cat <<EOF

Installé. Commandes utiles :
  tail -f ~/trader-donnees/paris_script.txt      # suivre le journal
  journalctl -u trader-simule -f                 # suivre l'exécution
  sudo systemctl stop trader-simule              # arrêter
  cd ~/trader && git pull && sudo systemctl restart trader-simule   # mettre à jour
EOF
