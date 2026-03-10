# 🤖 Trading Bot — Roadmap & Suivi des fonctionnalités

> **Convention pour l'agent IA :**
> - `- [ ]` = à faire
> - `- [x]` = terminé (ne pas supprimer, garder la trace)
> - Cocher une case dès qu'une fonctionnalité est **entièrement** implémentée, testée et commitée
> - Ne jamais supprimer une ligne — même terminée, elle reste pour l'historique

---

## 🔴 PRIORITÉ 1 — Bot fonctionnel en production

> Objectif : le bot passe des trades réels sur Hyperliquid de façon stable et fiable

- [x] Structure du projet Python modulaire (`main.py`, `strategy.py`, `risk_manager.py`, `hyperliquid_client.py`, `logger.py`, `config.py`)
- [x] Intégration SDK Hyperliquid (`hyperliquid-python-sdk`)
- [x] Stratégie de trading implémentée avec indicateurs techniques (EMA, RSI, ATR)
- [x] Risk management : stop-loss ATR×2, take-profit RR 1:2, position sizing 1% du capital
- [x] Trailing stop
- [x] Boucle principale avec gestion des erreurs et retry automatique (backoff exponentiel)
- [x] Logs rotatifs dans `logs/` (fichier, pas encore base de données)
- [x] Dockerisation : `docker-compose.dev.yml` et `docker-compose.prod.yml`
- [x] Variable `ENVIRONMENT` avec deux modes : `testnet` / `production`
- [x] En mode `testnet` : URL automatiquement `https://api.hyperliquid-testnet.xyz`
- [x] En mode `production` : avertissement au démarrage + délai 5 secondes
- [x] Validation au démarrage : refus si `LEVERAGE > 3` en production
- [x] `docker-compose.testnet.yml` avec service `bot-testnet` dédié
- [x] README complet : installation, variables `.env`, guide testnet, risk management
- [x] Récupération automatique du capital depuis l'API (portfolio fallback)
- [ ] **Valider que le bot passe des trades réels sur testnet sans erreur**
- [ ] **Valider que le bot passe des trades réels sur production avec petit capital**

---

## 🟠 PRIORITÉ 2 — Backtesting

> Objectif : pouvoir tester et optimiser la stratégie sur données historiques sans toucher au code de prod

- [ ] Dossier `backtest/` indépendant du code du bot (aucun import croisé)
- [ ] Script `backtest/run_backtest.py` :
  - [ ] Récupération des données historiques OHLCV via `ccxt` (Binance, minimum 1 an en 1h)
  - [ ] Sauvegarde locale des données en CSV dans `backtest/data/` pour éviter les re-téléchargements
  - [ ] Réimplémentation de la stratégie au format `backtesting.py`
  - [ ] Inclusion des frais Hyperliquid (0.05% taker entrée + sortie)
  - [ ] Rapport complet : win rate, profit factor, max drawdown, Sharpe ratio, nb trades, rendement vs Buy & Hold
  - [ ] Graphique HTML interactif des trades dans `backtest/results/`
- [ ] Script `backtest/optimize.py` :
  - [ ] Test de combinaisons de paramètres (EMA fast, EMA slow, RSI period)
  - [ ] Split 70% optimisation / 30% validation pour éviter l'overfitting
  - [ ] Tableau des 10 meilleures combinaisons par Sharpe ratio
- [ ] `backtest/requirements.txt` séparé (`backtesting`, `ccxt`)

---

## 🟡 PRIORITÉ 3 — Persistance Supabase

> Objectif : enregistrer chaque trade en base de données pour le suivi des performances et la déclaration fiscale
> **Note : un wallet dédié par bot — chaque bot a sa propre entrée dans la table `wallets`**

- [ ] Création du projet Supabase et récupération des clés
- [ ] Fichier `supabase/schema.sql` avec les trois tables :
  - [ ] Table `wallets` : `id`, `address`, `label`, `created_at`
  - [ ] Table `trades` : `id`, `wallet_id`, `pair`, `direction`, `status`, `entry_price`, `exit_price`, `quantity`, `notional_usd`, `leverage`, `stop_loss`, `take_profit`, `close_reason`, `pnl_usd`, `pnl_pct`, `fees_usd`, `funding_paid_usd`, `opened_at`, `closed_at`, `timeframe`, `strategy`, `environment`
  - [ ] Table `daily_snapshots` : `id`, `wallet_id`, `date`, `capital_usdc`, `open_positions`, `daily_pnl_usd`, `daily_fees_usd`, `daily_funding_usd`, `cumulative_pnl_usd`
- [ ] Fichier `supabase/rls_policies.sql` avec Row Level Security activé
- [ ] Nouveau fichier `supabase_logger.py` :
  - [ ] Méthode `register_wallet()` — insère le wallet au démarrage si inexistant, retourne son UUID
  - [ ] Méthode `log_trade_open()` — insère un trade avec status "open"
  - [ ] Méthode `log_trade_close()` — met à jour le trade à la fermeture (PnL, fees, funding, close_reason)
  - [ ] Méthode `log_daily_snapshot()` — upsert du snapshot quotidien à minuit UTC
  - [ ] Fallback : si Supabase est indisponible, logger dans le fichier log et continuer sans crasher
- [ ] Intégration dans `main.py` : appel `register_wallet()` au démarrage
- [ ] Intégration à l'ouverture de chaque position : `log_trade_open()`
- [ ] Intégration à la fermeture de chaque position : `log_trade_close()`
- [ ] Snapshot quotidien automatique à minuit UTC
- [ ] Mise à jour des variables `.env` et `docker-compose` avec `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `WALLET_ADDRESS`
- [ ] Mise à jour du README : section "Base de données", section "Données fiscales"

---

## 🟢 PRIORITÉ 4 — Dashboard Frontend

> Objectif : visualiser les performances de tous les bots dans une interface web
> **Architecture : repo séparé, se connecte directement à Supabase — aucun service front dans les docker-compose du bot**

- [ ] Créer un nouveau repo `trading-dashboard/` (séparé du repo bot)
- [ ] Setup React + TypeScript + Vite + Tailwind
- [ ] Connexion directe à Supabase depuis le front (clé `anon` publique)
- [ ] Page d'authentification (Supabase Auth)
- [ ] Page principale — vue globale tous bots :
  - [ ] Liste des wallets/bots avec leur label
  - [ ] PnL cumulé par bot
  - [ ] Graphique d'évolution du capital dans le temps
- [ ] Page détail par bot :
  - [ ] Liste de tous les trades (filtrables par période, paire, direction)
  - [ ] Win rate, profit factor, max drawdown, Sharpe ratio
  - [ ] Répartition des trades par close_reason (SL / TP / signal)
  - [ ] Comparaison des environnements (testnet vs production)
- [ ] Export CSV des trades pour déclaration fiscale
- [ ] Déploiement sur Vercel ou Netlify

---

## 🔵 PRIORITÉ 5 — Améliorations de la stratégie

> Objectif : améliorer la qualité des signaux une fois le bot stable en production

- [ ] Divergences RSI (au lieu de simples niveaux surachat/survente)
- [ ] Analyse multi-timeframe : 4H pour la direction, timeframe configuré pour l'entrée
- [ ] Retracements Fibonacci automatiques pour les zones d'entrée
- [ ] Support/résistance dynamique avec SMMA 50 et 200
- [ ] Trailing stop structurel (derrière les points bas/hauts structurels)
- [ ] Scaling out : fermeture partielle (50%) si momentum faible avant TP

---

## ⚪ PRIORITÉ 6 — Futures (version lointaine)

> À ne pas implémenter avant que tout le reste soit stable et profitable

- [ ] Support multi-DEX : modèle Adapter pour ajouter dYdX, GMX, Vertex
  - [ ] Interface commune `BaseExchangeClient` abstraite
  - [ ] Adapter Hyperliquid (migration du code existant)
  - [ ] Adapter dYdX v4
- [ ] Filtre macro fondamental :
  - [ ] Récupération données FRED API (CPI, taux directeurs, bond yields)
  - [ ] Score de biais directionnel mis à jour toutes les 4h
  - [ ] Utilisation du biais comme filtre sur les signaux techniques
- [ ] Alertes Telegram : notification à chaque trade ouvert/fermé
- [ ] Support multi-bots sur le même VPS avec stratégies différentes
