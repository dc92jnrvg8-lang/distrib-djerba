# Djerba OS — surveillance de l'agent avec Uptime Kuma

Ajoutez un moniteur :

* **Type** : HTTP(s) - Mot-clé
* **URL** : http://127.0.0.1:9464/health
* **Mot-clé** : `"status": "ok"`
* **Intervalle** : 60 s

`/health` répond 200 et `"status": "ok"` quand le bus de contexte est à jour et
que le démon root répond ; sinon 503 et `"status": "degraded"`, avec le détail
des contrôles dans `checks`.
