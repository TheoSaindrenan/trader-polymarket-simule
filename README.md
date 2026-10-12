# Trader simulé Polymarket

Expérience de trading **fictive** sur les marchés de prédiction Polymarket. Elle part de 50 $ fictifs. Le script lit uniquement des API publiques et ne place aucun ordre. Il ne se connecte à aucun compte de trading.

Toutes les 10 minutes, GitHub Actions lance `trader_simule.py --une-fois`, puis enregistre dans le dépôt :

- `paris_script.txt` : le journal (entrées, sorties, raisons, bilans) ;
- `etat_script.json` : la cagnotte, les paris ouverts et le cache des analyses.

Les règles complètes sont décrites en tête de `trader_simule.py`.

## Mise en route

1. **Secrets.** Dans *Settings → Secrets and variables → Actions → Secrets*, ajoute :
   - `ANTHROPIC_API_KEY` (obligatoire) ;
   - `X_BEARER_TOKEN` (facultatif ; sans lui, l'étape X est sautée).
2. **Variables** (facultatif). Dans l'onglet *Variables* :
   - `MODELE_CLAUDE` (défaut `claude-sonnet-5-5`) ;
   - `ANALYSES_MAX`, le nombre de nouvelles analyses par cycle (défaut 20).
3. **Premier cycle.** Dans l'onglet *Actions*, ouvre « Trader simulé » et clique sur *Run workflow*.

## Arrêt

Si la cagnotte perd 40 % depuis son plus haut, ou tombe à 0, le script :

1. liquide fictivement les positions ;
2. écrit un bilan dans `paris_script.txt` ;
3. ouvre une issue « Trader simulé arrêté » et désactive le workflow.

Pour recommencer, supprime `etat_script.json` et `ALERTE_ARRET.txt`, puis réactive le workflow.

Pour un bilan à la demande en local : `python3 trader_simule.py --bilan`.

## Limites

- **Horaires.** GitHub ne garantit pas l'heure exacte des tâches planifiées : un cycle peut avoir quelques minutes de retard, voire être sauté aux heures chargées.
- **Coût.** Chaque nouvelle analyse coûte deux appels à l'API Claude, plus des recherches web. Réduis `ANALYSES_MAX` pour limiter la facture.
