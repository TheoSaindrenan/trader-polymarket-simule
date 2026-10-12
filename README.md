# Trader simulé Polymarket

Expérience de trading **fictive** sur les marchés de prédiction Polymarket. Elle part de 50 $ fictifs. Le script lit uniquement des API publiques et ne place aucun ordre. Il ne se connecte à aucun compte de trading.

Toutes les 10 minutes, GitHub Actions lance `trader_simule.py --une-fois`, puis enregistre dans le dépôt :

- `paris_script.txt` : le journal (entrées, sorties, raisons, bilans) ;
- `etat_script.json` : la cagnotte, les paris ouverts et le cache des analyses.

Les règles complètes sont décrites en tête de `trader_simule.py`.

## Mise en route

1. **Secrets.** Dans *Settings → Secrets and variables → Actions → Secrets*, ajoute :
   - `ANTHROPIC_API_KEY` (obligatoire) ;
   - `X_BEARER_TOKEN` (facultatif ; sans lui, l'étape X est sautée). C'est une clé de l'API X pour développeurs, facturée à l'usage, distincte de l'abonnement X Premium.
2. **Variables** (facultatif). Dans l'onglet *Variables* :
   - `MODELE_CLAUDE` (défaut `claude-sonnet-5-5`) ;
   - `ANALYSES_MAX`, le nombre de nouvelles analyses par cycle (défaut 20) ;
   - `X_BUDGET_JOUR`, le budget X en dollars par jour (défaut 1) ;
   - `X_POSTS`, le nombre de posts lus par recherche X (défaut 20).
3. **Premier cycle.** Dans l'onglet *Actions*, ouvre « Trader simulé » et clique sur *Run workflow*.

## Arrêt

Si la cagnotte perd 40 % depuis son plus haut, ou tombe à 0, le script :

1. liquide fictivement les positions ;
2. écrit un bilan dans `paris_script.txt` ;
3. ouvre une issue « Trader simulé arrêté » et désactive le workflow.

Pour recommencer, supprime `etat_script.json` et `ALERTE_ARRET.txt`, puis réactive le workflow.

Pour un bilan à la demande en local : `python3 trader_simule.py --bilan`.

## Utilisation de X

Chaque marché est d'abord estimé sans X. X n'est consulté que pour les finalistes : les marchés dont l'écart provisoire atteint déjà 5 points, et les paris ouverts. La décision d'entrée se prend sur l'estimation refaite avec X.

Le journal indique pour chaque pari l'estimation sans X et l'estimation avec X. On peut ainsi mesurer ce que X apporte réellement.

Une fois le budget du jour atteint, l'analyse continue sans X jusqu'au lendemain (UTC).

## Limites

- **Horaires.** GitHub ne garantit pas l'heure exacte des tâches planifiées : un cycle peut avoir quelques minutes de retard, voire être sauté aux heures chargées.
- **Coût.** Chaque nouvelle analyse coûte deux appels à l'API Claude, plus des recherches web. Réduis `ANALYSES_MAX` pour limiter la facture.
