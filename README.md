# EU Premier — Classement Contender & Invite

Site statique qui affiche le classement Valorant Premier (divisions Contender
et Invite, région EU), mis à jour automatiquement via [HenrikDev API](https://docs.henrikdev.xyz).

## Mise en route (une seule fois, ~10 min)

### 1. Obtenir une clé API HenrikDev
1. Rejoins le Discord officiel : lien sur https://github.com/Henrik-3/unofficial-valorant-api
2. Génère une clé "Basic" sur https://api.henrikdev.xyz/dashboard/ (instantané)

### 2. Créer le dépôt GitHub
1. Crée un nouveau dépôt (public ou privé) sur GitHub
2. Mets-y tous les fichiers de ce dossier (`index.html`, `fetch_premier_data.py`,
   `data/`, `.github/`)

### 3. Ajouter ta clé API en secret
Dans le dépôt : **Settings → Secrets and variables → Actions → New repository secret**
- Nom : `HENRIKDEV_API_KEY`
- Valeur : ta clé obtenue à l'étape 1

### 4. Activer GitHub Pages
**Settings → Pages → Source : "GitHub Actions"**

### 5. Lancer le premier passage
**Onglet Actions → "Update Premier data and deploy site" → Run workflow**

Le site sera disponible à `https://<ton-user>.github.io/<nom-du-repo>/`
et se remettra à jour tout seul 2 fois par jour (6h et 18h UTC) sans que tu
aies rien à faire.

## Tester en local avant de publier

```bash
export HENRIKDEV_API_KEY="ta_clé"
python3 fetch_premier_data.py          # classement seulement
python3 fetch_premier_data.py --with-history   # + forme récente (plus lent)

python3 -m http.server 8000            # puis ouvre localhost:8000
```

## Limites connues (honnêtes, pour éviter les surprises)

- **Détection Contender/Invite** : le script essaie de lire un nom de
  division explicite renvoyé par l'API ; si l'API ne le fournit que sous
  forme de numéro, il retombe sur "les 2 divisions les plus hautes par
  conférence". Vérifie le résultat du premier run et ajuste
  `TARGET_DIVISION_NAMES` / `FALLBACK_TOP_N_DIVISIONS` dans
  `fetch_premier_data.py` si le filtrage ne tombe pas juste.
- **Noms des joueurs** : le roster renvoyé par l'API leaderboard est une
  liste d'identifiants internes (PUUID), pas des pseudos. Résoudre chaque
  PUUID en pseudo demande un appel API par joueur — non fait par défaut
  pour rester sous la limite de requêtes (30 à 90/min selon ta clé). C'est
  ajoutable si tu veux, mais ça multipliera fortement le nombre d'appels.
- **Stats détaillées type vlr.gg (ACS, K/D, headshot%...)** : ces chiffres
  viennent du détail de chaque match individuel, pas des endpoints Premier
  eux-mêmes. Les récupérer pour toutes les équipes Contender/Invite d'EU
  demanderait un très grand nombre d'appels API (un par match, par équipe).
  Le site affiche pour l'instant le classement, les points et la forme
  récente (victoires/défaites) — une bonne base à laquelle on peut ajouter
  les stats de match plus tard si tu veux investir dans un plan API avec
  une limite plus haute.
