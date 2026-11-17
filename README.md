# Portail de suivi des applications

Portail privé pour gérer les fiches des applications et consulter leur
supervision. La pile associe FastAPI, Prometheus, Grafana et des collecteurs
OpenTelemetry à des sondes et exporters optionnels.

> **Déploiement sur un serveur privé :** la configuration fournie est destinée
> au développement/local et ne doit pas être exposée telle quelle. Consultez
> [Sécurité et préparation du déploiement](#sécurité-et-préparation-du-déploiement)
> avant d'ouvrir l'accès à d'autres utilisateurs.

## Fonctions

- Registre des applications avec responsable, version, dépôt, URL et historique
  des modifications.
- Rôles **Admin**, **Moniteur** et **Checker** : les Admins gèrent les comptes
  et les fiches, les Moniteurs gèrent les fiches et les Checkers consultent
  Grafana.
- Tableaux Grafana, état de santé et vérification des signaux reçus.
- Collecte des métriques Prometheus et OpenTelemetry, avec traces et journaux
  lorsque les applications les exportent.
- Alertes Prometheus relayées par Alertmanager vers Slack lorsque le webhook
  est configuré.

Une fiche décrit une application, mais ne l'instrumente pas automatiquement.
L'application doit exposer des métriques, exporter des signaux OpenTelemetry,
ou être vérifiée par une sonde/exporter configuré.

## Architecture

| Composant | Fonction |
| --- | --- |
| Caddy | Reverse proxy local vers le portail, Grafana et Prometheus |
| Portail (`demo-app`) | Fiches, comptes, rôles, historique et authentification |
| Prometheus | Collecte, stockage et requêtes sur les métriques |
| Grafana | Tableaux de bord provisionnés depuis `grafana/provisioning/dashboards/` |
| Node Exporter | CPU, mémoire, disques, réseau et uptime de l'hôte Linux visible |
| cAdvisor | Ressources des conteneurs Docker; optionnel si cette vue n'est pas nécessaire |
| OpenTelemetry Collector | Réception OTLP; export vers Prometheus, Tempo et Loki |
| OBI | Observation eBPF de certains appels du portail; nécessite un hôte Linux compatible |
| Blackbox Exporter | Sondes HTTP configurées dans `prometheus.yml` et `blackbox.yml` |
| Alertmanager | Regroupement et livraison des alertes configurées |
| Tempo / Loki | Stockage des traces / journaux exportés |
| Grafana Alloy | Export optionnel des métriques vers Grafana Cloud |

Les services ne sont pas tous nécessaires pour chaque installation. Les
dashboards applicatifs et les collecteurs associés dépendent des sources
configurées. Sous Docker Desktop Windows, Node Exporter observe la VM Linux de
Docker, pas le système Windows.

## Démarrage local

### Prérequis

- Docker avec Docker Compose v2.
- Ressources indicatives pour l'ensemble de la pile : 4 CPU, 8 Go de mémoire et
  au moins 10 Go d'espace disque; les besoins dépendent de la rétention et du
  volume de métriques.

### Configuration et lancement (PowerShell)

```powershell
Copy-Item .env.example .env
notepad .env
docker compose config -q
docker compose up -d --build
docker compose ps
```

Remplacez les valeurs d'exemple dans `.env` par des secrets uniques. Le secret
de session doit compter au moins 32 caractères et le mot de passe Admin initial
au moins 12 caractères. Ne versionnez jamais `.env`.

Par défaut, le portail est servi sur <http://localhost:8080/>. Selon votre
configuration, Grafana est disponible sous `/grafana/` et Prometheus sous
`/prometheus/` sur le même hôte. Prometheus est réservé aux rôles Admin et
Moniteur; les Checkers n'accèdent qu'aux tableaux Grafana. Le compte Admin
initial est créé uniquement lors de l'initialisation de la base; modifier
`PORTAL_ADMIN_PASSWORD` après coup ne change pas le mot de passe d'un compte
existant.

### Arrêt et données

```powershell
docker compose down
```

Cette commande arrête et supprime les conteneurs, mais conserve les volumes
nommés. **N'utilisez pas `docker compose down -v`** sauf si vous souhaitez
effacer définitivement les données.

| Volume | Données |
| --- | --- |
| `portal_data` | Base SQLite du portail |
| `prometheus_data` | Séries Prometheus, rétention configurée à 30 jours |
| `grafana_data` | Configuration et données Grafana |
| `loki_data` | Journaux, rétention configurée à 7 jours |
| `tempo_data` | Traces, rétention configurée à 7 jours |
| `alertmanager_data` | État persistant d'Alertmanager |
| `caddy_data`, `caddy_config` | Données et configuration interne de Caddy |

Les historiques de métriques, journaux et traces consomment de l'espace disque.
Surveillez les volumes et prévoyez des sauvegardes régulières. Faites une
sauvegarde cohérente de SQLite et testez sa restauration avant toute mise à
jour importante.

## Utilisation du portail

### Rôles

- **Admin** : crée/désactive les comptes et gère les fiches.
- **Moniteur** : crée et gère les fiches, sans gérer les comptes.
- **Checker** : accès en lecture aux tableaux Grafana autorisés.

Les mots de passe sont stockés sous forme hachée. Les formulaires qui modifient
des données sont protégés contre les requêtes CSRF. Les sessions expirent après
huit heures. Pour un Checker, la déconnexion depuis Grafana efface également
la session proxy du portail et renvoie vers `/login`.

### Ajouter une application

1. Dans **Applications**, créer une fiche et renseigner le nom, le responsable,
   les détails de l'application et son URL si elle possède un site web.
2. Choisir un nom de service stable, puis le reprendre à l'identique dans
   `OTEL_SERVICE_NAME` ou dans le label `service_name` des métriques.
3. Raccorder une source de données :
   - OpenTelemetry SDK/auto-instrumentation vers le Collector;
   - endpoint Prometheus `/metrics` configuré comme cible;
   - sonde Blackbox pour disponibilité et temps de réponse HTTP;
   - exporter adapté à l'hôte, aux conteneurs ou au produit.
4. Générer du trafic ou des opérations de test, vérifier les séries reçues, puis
   créer/adapter un tableau Grafana à partir des métriques réellement présentes.

La fiche affiche l'état disponible à partir des données collectées; un état
« aucun signal » ne signifie pas que l'application est arrêtée si aucune source
n'a encore été raccordée.

### Intégration OpenTelemetry

Pour une application dans le même réseau Docker que le Collector, la pile
accepte OTLP/HTTP sur `http://otel-collector:4318` et OTLP/gRPC sur
`otel-collector:4317`. Les conteneurs doivent rejoindre le réseau partagé
`telemetry` (le nom effectif se vérifie avec `docker network ls`). Pour un
processus exécuté sur le même hôte, les ports OTLP sont publiés sur la boucle
locale par défaut : `http://127.0.0.1:4318` ou `127.0.0.1:4317`.

Les ports OTLP ne sont pas configurés pour un accès distant sécurisé. Ne les
ouvrez pas sur Internet. Pour une application sur un autre serveur, mettre en
place une liaison privée et configurer TLS/authentification sur le chemin
jusqu'au Collector. Les métriques métier nécessitent généralement une
instrumentation explicite dans le code.

La [fiche d'installation OpenTelemetry sur un serveur](docs/fiche-installation-opentelemetry.md)
contient les exemples de variables, un exemple Python/FastAPI et les étapes de
validation. Une fiche Admin complémentaire est disponible dans le portail à
`/admin/integration-guide`.

## Tableaux et alertes

Les tableaux provisionnés se trouvent dans le dossier **Applications** de
Grafana. Ils comprennent notamment la supervision de la plateforme, l'hôte
Linux (Node Exporter), les conteneurs (cAdvisor) et des services configurés.
Les tableaux n'inventent pas de données : un panneau reste vide si sa métrique
ou sa cible n'est pas disponible.

Pour un contrôle rapide, ouvrir Grafana **Explore**, choisir Prometheus et
essayer :

```promql
up
up{job="node-exporter"}
node_memory_MemAvailable_bytes
node_filesystem_avail_bytes
```

`up == 1` confirme que Prometheus peut interroger une cible; cela ne prouve pas
que toutes ses métriques métier sont présentes. Pour les sondes Blackbox,
vérifier également `probe_success`.

Les règles d'alerte sont définies dans `prometheus-alerts.yml`. Alertmanager
utilise le secret `SLACK_WEBHOOK_URL` pour envoyer les notifications Slack.
Sans webhook valide, la collecte continue, mais les notifications Slack ne
seront pas livrées.

## Export optionnel vers Grafana Cloud

L'export des métriques est optionnel et se fait par le profil Alloy. Renseignez
dans `.env` les valeurs obtenues dans Grafana Cloud :

```dotenv
GRAFANA_CLOUD_PROMETHEUS_URL=https://prometheus-<region>.grafana.net/api/prom/push
GRAFANA_CLOUD_PROMETHEUS_USER=<metrics-instance-id>
GRAFANA_CLOUD_PROMETHEUS_TOKEN=<api-token>
```

Lancez les services avec le fichier d'override :

```powershell
docker compose -f docker-compose.yml -f docker-compose.grafana-cloud.yml --profile grafana-cloud up -d
```

Ce profil exporte les métriques Prometheus vers Grafana Cloud. Il ne transfère
pas les tableaux Grafana, les journaux Loki ou les traces Tempo; leur export
nécessite une configuration distincte. Gardez le token uniquement dans le
mécanisme de secrets de l'environnement ou dans un `.env` non versionné.

## Sécurité et préparation du déploiement

**La configuration locale ne doit pas être publiée telle quelle sur le réseau
privé ou Internet.** Avant le déploiement, vérifier et adapter les points
suivants :

1. **Réseau et HTTPS** — Caddy sert HTTP sur le port 80. Le Compose lie par
   défaut le port à `127.0.0.1`; configurez `PORTAL_BIND_ADDRESS` avec une
   adresse de l'hôte seulement si le pare-feu et le reverse proxy/TLS du serveur
   sont prêts. Ne publiez pas directement le port HTTP sur Internet.
2. **Cookie de session** — Quand le site est servi en HTTPS, définissez
   `PORTAL_SESSION_COOKIE_SECURE=true` dans `.env`; laissez-le à `false` pour le
   HTTP local. Vérifiez connexion, navigation et déconnexion après le changement.
3. **Prometheus** — La route Caddy `/prometheus/` exige une session Admin ou
   Moniteur; les Checkers sont refusés. Le récepteur remote-write est désactivé
   côté Prometheus; Alloy continue d'envoyer les métriques via son récepteur
   interne. Ne publiez pas les ports internes de Prometheus sur l'hôte.
4. **Secrets et comptes** — Créer des secrets longs et uniques pour le portail,
   Grafana, SMTP, Slack et Grafana Cloud. Protéger les fichiers d'environnement.
   Le changement du mot de passe Admin dans `.env` ne réinitialise pas le compte
   existant dans SQLite.
5. **Cibles Prometheus** — Les cibles des sondes externes et de Jenkins sont
   externalisées dans `prometheus/targets/` et laissées vides par défaut.
   Ajoutez uniquement des adresses accessibles depuis le réseau Docker dans
   les fichiers YAML correspondants. Exemple pour
   `prometheus/targets/jenkins-blackbox.yml` :

   ```yaml
   - targets: ["https://jenkins.example.net/login"]
     labels:
       service_name: jenkins
   ```

   Pour les métriques Jenkins, ajoutez `jenkins.example.net` dans
   `jenkins-metrics.yml` avec le même label `service_name`.
6. **Sources OTLP distantes** — Les ports OTLP sont liés à la boucle locale et
   ne sont pas configurés avec TLS/authentification. Un serveur distant ne
   peut pas les utiliser avant une configuration réseau sécurisée.
7. **Droits système** — cAdvisor fonctionne avec `privileged: true` et des
   montages système; OBI utilise le PID hôte et des capacités Linux étendues.
   Évaluer ces droits sur le serveur et désactiver les composants inutiles.
8. **Données et disponibilité** — La base est SQLite : utiliser une seule
   instance du portail, sauvegarder les volumes et tester une restauration.
   Une mise à jour ou une migration peut provoquer une interruption; le
   déploiement ne fournit pas de haute disponibilité.

## Exploitation et dépannage

Après démarrage, vérifiez `docker compose ps`, la page `/health` (qui confirme
aussi la disponibilité de SQLite), puis l'état des cibles dans Prometheus et
les panneaux Grafana. Après activation de HTTPS, confirmez que le cookie de
session porte l'attribut `Secure` et que les trois rôles conservent leurs accès
prévus.

Voir l'état des conteneurs et leurs journaux :

```powershell
docker compose ps
docker compose logs --tail 100 demo-app caddy grafana prometheus otel-collector
```

Vérifier le portail localement :

```powershell
Invoke-RestMethod http://localhost:8080/health
```

- **Aucune métrique** : vérifier que l'application émet des signaux, que le
  nom de service correspond et que la cible est joignable depuis le bon
  conteneur/réseau.
- **`up == 0`** : vérifier DNS, adresse, port, protocole, TLS et identifiants
  éventuels de la cible.
- **Tableau vide** : vérifier la période Grafana, la requête PromQL et les
  labels présents dans la série.
- **Alerte non reçue** : vérifier le secret Slack, les journaux d'Alertmanager
  et l'état de la règle dans Prometheus.
- **Données perdues après redémarrage** : vérifier les volumes Docker utilisés
  et ne pas supprimer ces volumes lors des commandes de nettoyage.

Une modification d'une configuration montée en lecture seule s'applique en
recréant le service concerné, par exemple :

```powershell
docker compose up -d --force-recreate prometheus
```

Pour préserver les données, éviter les suppressions de volumes et valider les
fichiers avec `docker compose config -q` avant de redémarrer les services.

## Tests

Lancer les tests d'intégration du portail dans un conteneur temporaire :

```powershell
docker build -f demo-app\Dockerfile.test -t portail-tests demo-app
docker run --rm portail-tests
```

Les tests utilisent une base SQLite temporaire et ne modifient pas le volume
`portal_data`.
