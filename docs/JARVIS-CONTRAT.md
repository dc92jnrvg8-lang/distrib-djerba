# Contrat d'adaptateur Jarvis ↔ Djerba OS (version 1)

> **Statut.** Aucune information sur l'API réelle de Jarvis n'était disponible
> lors de la phase 3. Ce document définit le **contrat minimal** que Djerba
> implémente et teste (adaptateur de référence `core/engines.py`, serveur
> factice dans `tests/test-djerba-ai.py`). Si l'API réelle de Jarvis diffère,
> seul l'adaptateur change : les outils, la politique et l'historique de
> Djerba restent identiques.

Deux sens, indépendants :

1. **Jarvis comme moteur de Djerba** : Djerba envoie la conversation et ses
   outils à Jarvis, Jarvis propose des appels d'outils, Djerba les exécute
   sous sa politique.
2. **Djerba comme serveur d'outils pour Jarvis** (MCP) : Jarvis appelle les
   capacités de Djerba ; chaque appel passe par la politique et l'audit de
   Djerba, avec l'identité de l'appelant.

Dans les deux sens : réseau privé seulement (Tailscale `100.64.0.0/10`,
`*.ts.net`, réseau local, boucle locale), jeton par appairage, révocable.

---

## 1. Jarvis comme moteur (Djerba → Jarvis)

Jarvis expose une API **compatible OpenAI** sous `/v1` :

| Méthode | Chemin | Rôle |
|---|---|---|
| `POST` | `/v1/pair` | Appairage : `{"code": "123456", "client": "djerba-os", "name": "<nom de la machine>"}` → `{"token": "<jeton ≥ 16 caractères>", "name": "Jarvis", "model": "<modèle>", "expires": <horodatage ou null>}` |
| `GET` | `/v1/health` | `{"name": "Jarvis", "status": "ok"}` (Bearer) |
| `GET` | `/v1/models` | `{"data": [{"id": "<modèle>"}]}` (Bearer) |
| `POST` | `/v1/chat/completions` | Format OpenAI, `"stream": true`, réponses SSE (`data: {...}`, `data: [DONE]`), appels d'outils `tool_calls` (Bearer) |

* **Appairage** : Jarvis affiche un code à **6 chiffres** (valable quelques
  minutes) ; l'utilisateur le saisit dans Djerba
  (`djerba-ai engines pair-jarvis http://jarvis.tailnet.ts.net:8000 123456`).
  Le démon root de Djerba échange le code contre le jeton et l'enregistre
  dans `/etc/djerba/engines.json` (root, 0600) : la session de l'utilisateur
  ne voit jamais le jeton.
* **Authentification** : `Authorization: Bearer <jeton>` sur toutes les
  requêtes après appairage. Révocation côté Djerba :
  `djerba-ai engines remove jarvis`.
* **Sans appels d'outils** : si Jarvis ne renvoie jamais de `tool_calls`
  (profil mesuré à l'ajout), Djerba bascule en « JSON contraint » : la
  consigne décrit les outils et Jarvis répond `{"outil": …, "arguments": …}`
  ou `{"reponse": …}`, validé strictement (`response_format: json_object`).
* **Confidentialité** : messages masqués par le DLP avant envoi ; contexte
  système envoyé, mémoire non (réglable par moteur). Une réponse anormale de
  Jarvis (outil non proposé, indices d'injection) plafonne la tâche au
  niveau 1.

## 2. Djerba comme serveur d'outils (Jarvis → Djerba, MCP)

Serveur **MCP** minimal (JSON-RPC 2.0 en HTTP, sans flux), lancé par
`djerba-ai mcp-serve` (service utilisateur `djerba-ai-mcp.service`, activé
par le réglage `mcp.enabled`), **lié à l'adresse Tailscale** de la machine
(port `mcp.port`, 8765 par défaut).

| Méthode | Chemin | Rôle |
|---|---|---|
| `POST` | `/pair` | `{"code": "123456", "name": "jarvis"}` → `{"token": "…", "client": "jarvis"}` (5 essais par minute au plus) |
| `POST` | `/mcp` | JSON-RPC MCP (Bearer) : `initialize`, `notifications/initialized`, `tools/list`, `tools/call` |

* **Appairage** : `djerba-ai mcp pair NOM` affiche un code à 6 chiffres
  (valable 5 minutes) et un QR (`djerba-mcp://<adresse>:<port>?code=…`) ;
  Jarvis l'échange contre un jeton. Liste et révocation :
  `djerba-ai mcp list`, `djerba-ai mcp revoke NOM`. Les jetons sont stockés
  hachés (SHA-256) par le démon root (`/etc/djerba/mcp-clients.json`, 0600).
* **`tools/list`** : toutes les capacités de Djerba, avec leur schéma
  (`inputSchema`) et leur niveau de politique dans la description.
* **`tools/call`** : `{"name": "...", "arguments": {...}}` → exécution par
  Djerba sous **sa** politique (origine `mcp-<client>`, plafond réglable par
  appelant, 2 par défaut), journal d'audit et de transactions (annulable).
  Résultat MCP : `{"content": [{"type": "text", "text": "<JSON du résultat>"}],
  "isError": <échec>}`.
* **Débit** : 60 appels par minute et par appelant.

## 3. Historique de Jarvis (import, phase 4)

| Méthode | Chemin | Rôle |
|---|---|---|
| `GET` | `/v1/history` | Historique des conversations (Bearer), 32 Mio au plus |

```json
{"conversations": [
  {"id": "c-42", "title": "Réseau du salon", "created": 1761600000,
   "messages": [{"role": "user", "content": "…", "created": 1761600000},
                {"role": "assistant", "content": "…", "created": 1761600005}]}
]}
```

* Lu par le démon root (`engines.jarvis_history`) : le jeton reste root, seul
  l'historique revient à la session, qui l'importe dans sa mémoire chiffrée
  (`djerba-ai history import`, sans fichier) ; ou depuis un fichier JSON du
  même format (`djerba-ai history import export.json`).
* Seuls les rôles `user` et `assistant` sont repris ; contenu passé au DLP ;
  dédoublonnage par `id` (un import répété n'ajoute rien).
* Les conversations importées sont attribuées au moteur Jarvis et peuvent
  être reprises avec n'importe quel moteur.

## 4. Ce dont Djerba a besoin pour brancher le vrai Jarvis

* L'adresse de son API et son protocole (HTTP compatible OpenAI, WebSocket,
  MCP…), son mode d'authentification ;
* s'il gère les appels d'outils et le flux ;
* le format réel de son historique (l'import suit pour l'instant le §3).
