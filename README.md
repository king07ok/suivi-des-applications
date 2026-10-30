# Suivi des applications

La phase 1 installe la pile locale de télémétrie : OpenTelemetry Collector,
Prometheus, Alertmanager, Tempo, Loki et Grafana derrière Caddy. Le portail
FastAPI et Grafana partagent le même domaine local.

La phase 2 ajoute les tableaux de bord Grafana « Suivi des applications » et
« Supervision de la plateforme », ainsi que des règles d'alerte Prometheus pour
les applications silencieuses, les erreurs, la latence et l'indisponibilité des
composants de supervision. Alertmanager relaie ces alertes vers Slack lorsque
le webhook est configuré.

La phase 3 ajoute le registre des applications, les fiches, l'historique des
modifications, l'authentification et les rôles Admin, Moniteur et Checker. La
base SQLite est conservée dans un volume Docker dédié.

## Prérequis

- Docker Desktop avec le plugin Docker Compose v2
- 4 processeurs, 8 Go de mémoire et au moins 10 Go d'espace disque libre

Les images et dépendances sont épinglées à des versions précises. Le premier
lancement télécharge les images ; ensuite, l'exploitation peut se faire sans
connexion externe tant que les images sont présentes localement.

## Démarrage (PowerShell)

```powershell
Copy-Item .env.example .env
notepad .env
docker compose config
docker compose up -d --build
docker compose ps
```

Remplacez les valeurs de démonstration de `GRAFANA_ADMIN_PASSWORD`,
`PORTAL_SESSION_SECRET_KEY` et `PORTAL_ADMIN_PASSWORD` avant un déploiement
partagé. Utilisez des secrets aléatoires différents pour chaque service.
Ne versionnez jamais `.env`. Le port web est `8080` par défaut et le Collector
est publié uniquement sur la boucle locale de la machine.

- Portail des applications : http://localhost:8080/
- Grafana : http://localhost:8080/grafana/
- Accès Checker à Grafana : mêmes identifiants que le portail; le rôle est
  limité à la consultation
- Compte technique Grafana : valeurs `GRAFANA_ADMIN_USER` et
  `GRAFANA_ADMIN_PASSWORD` du fichier `.env`
- Première connexion au portail : valeurs `PORTAL_ADMIN_EMAIL` et
  `PORTAL_ADMIN_PASSWORD` du fichier `.env`

Le compte administrateur initial est créé automatiquement au premier démarrage
de la base; les redémarrages suivants ne réinitialisent pas son mot de passe.
Connectez-vous puis créez les comptes Admin, Moniteur et Checker dans
**Comptes**. Les Admins et Moniteurs peuvent modifier leur mot de passe depuis
**Mon compte**.

## Vérification rapide

```powershell
Invoke-RestMethod http://localhost:8080/health
docker compose logs --since 5m otel-collector
```

Dans Grafana Explore, vérifiez :

- **Prometheus** : `stagiaires_app_requests_total`
- **Loki** : `{service_name="demo-stagiaire"}`
- **Tempo** : recherchez les traces par service `demo-stagiaire`

Les métriques applicatives sont exportées périodiquement ; attendez jusqu'à
15 secondes après l'ouverture du portail.

## Phase 3 : portail, comptes et applications

Le portail redirige les visiteurs non connectés vers la page de connexion.
Dans **Applications**, les comptes autorisés peuvent rechercher et filtrer les
fiches par nom, service, responsable, état d'archivage et période de stage.
Une fiche contient le nom du service OpenTelemetry, la description, le langage,
le dépôt Git, la version, le responsable et les dates de stage; elle conserve
également un historique des modifications. Les fiches sont archivées, et non
supprimées.

Les Admins peuvent créer les comptes et gérer toutes les fiches. Les Moniteurs
gèrent toutes les fiches, sans gérer les comptes. Les Checkers accèdent
uniquement aux tableaux de bord Grafana via leur compte du portail. Au
démarrage, les anciens rôles Stagiaire et Lecteur sont convertis en Moniteur et
Checker. Les mots de passe sont hachés avec
Argon2id; les formulaires sont protégés contre les requêtes CSRF, et les sessions
sont stockées dans un cookie signé `HttpOnly` avec `SameSite=Lax`.

La base du portail est stockée dans le volume Docker `portal_data`. La
notification par e-mail utilise SMTP avec STARTTLS. Les e-mails de bienvenue
et les avis de modification du mot de passe sont envoyés lorsque SMTP est
configuré; aucun mot de passe n'est inclus dans les messages. Sans configuration
SMTP, le portail continue de fonctionner et affiche que la notification n'a
pas été envoyée.

### Configuration SMTP

Renseignez les paramètres suivants dans votre fichier local `.env` (ne les
committez jamais) :

```dotenv
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=compte-smtp
SMTP_PASSWORD=mot-de-passe-ou-secret-d-application
SMTP_FROM=portail@example.com
SMTP_USE_STARTTLS=true
PORTAL_PUBLIC_URL=https://portail.example.com
```

Utilisez les paramètres SMTP fournis par votre service de messagerie. Si le
serveur ne demande pas d'authentification, laissez `SMTP_USERNAME` et
`SMTP_PASSWORD` vides. Le port 587 avec STARTTLS est recommandé. Après avoir
enregistré `.env`, appliquez la configuration puis vérifiez les journaux :

```powershell
docker compose up -d --build demo-app
docker compose logs --since 5m demo-app
```

Laissez `SMTP_HOST` vide pour désactiver l'envoi. Les erreurs de livraison
n'annulent pas la création du compte ni le changement de mot de passe; elles
sont journalisées et signalées dans le portail.

## Phase 4 : santé en direct et tableaux Grafana intégrés

Le registre affiche l'état de santé observé à l'ouverture. La fiche actualise
son état toutes les 30 secondes en interrogeant Prometheus : un battement reçu
depuis 60 secondes au plus est **Opérationnel**, de plus de 60 à 300 secondes
**À surveiller**, puis **Indisponible** au-delà de 300 secondes. Sans métrique, le portail indique
**Aucun signal**; si Prometheus ne répond pas, l'état reste explicitement
**inconnu**. Ces seuils sont distincts des règles d'alerte de la phase 2.

La fiche intègre le tableau de bord Grafana filtré sur le service. Une session
Grafana reste requise pour afficher ses panneaux. L'application interroge
Prometheus par une liaison Docker interne dédiée. Son interface est aussi
accessible par le chemin `/prometheus/` du portail; Caddy relaie ces requêtes
vers Prometheus avec un préfixe cohérent. Cela évite les liens `localhost`
qui, depuis un autre poste, pointeraient vers l'ordinateur de l'utilisateur.
Le port Prometheus `9090` reste lié à la boucle locale et n'est pas publié
directement sur le réseau ou Internet.

### Raccordement OpenTelemetry des nouvelles applications

Le Collector OTLP est partagé : il reçoit automatiquement les métriques,
journaux et traces de tout service instrumenté, sans ajout de cible ni
modification de sa configuration à chaque nouvelle fiche. Le tableau Grafana
détecte automatiquement les applications depuis toutes les métriques
`stagiaires_*` portant un attribut `service_name` (et non plus uniquement le
battement de cœur). Les panneaux Prometheus et Loki filtrent sur le
`service_name` de la fiche. Celle-ci
présente les variables OTLP correspondantes pour les conteneurs connectés au
réseau `stagiaires-observability_telemetry` ou pour une application lancée
directement sur la machine.

### Atelier de code isolé pour les sites de test

Sur la fiche d'une application active, le bouton **Atelier de code** ouvre un
éditeur HTML/CSS/JavaScript. Les modifications sont enregistrées dans la base
du portail, par application, avec un numéro de révision; elles ne changent pas
le dépôt Git ni les fichiers du serveur. Seuls les Admins et les Moniteurs peuvent enregistrer du code. Les Checkers
ne peuvent pas ouvrir l'éditeur.

L'aperçu s'exécute dans une iframe sandboxée du navigateur avec une politique
de sécurité qui bloque les connexions réseau, les formulaires et l'accès au
contexte du portail. Le code ne s'exécute jamais sur le serveur. Chaque fichier
est limité à 20 000 caractères. L'atelier est un espace de prototypage local :
il ne déploie pas le site modifié; publiez et instrumentez séparément son dépôt.

L'enregistrement d'une fiche ne peut pas installer un SDK ni modifier le dépôt
de l'application. Pour démarrer l'export, appliquez les variables affichées
dans sa fiche et activez l'instrumentation OpenTelemetry de son langage pour les
métriques et les journaux (ainsi que les traces). Pour Python, le démarrage peut
utiliser `opentelemetry-distro`, `opentelemetry-exporter-otlp` et
`opentelemetry-instrument`; les bibliothèques d'instrumentation propres au
framework doivent aussi être installées. Une fois ce raccordement fait, les
nouvelles données sont découvertes automatiquement par Prometheus, Loki et
Grafana. Une application doit émettre au moins une métrique avec l'attribut
`service.name` correspondant pour apparaître dans le filtre partagé; les
journaux sont indexés sous le même nom de service dans Loki.

Le bouton **Vérifier le raccordement** sur une fiche interroge Prometheus et
indique si les métriques HTTP, le battement, les mesures SQL et celles des files
sont reçus; il précise aussi si les alertes Slack sont activées au niveau de la
plateforme. Cette vérification est en lecture seule : elle ne peut pas installer
à distance un SDK ni modifier le code ou le déploiement d'une application.
Après l'instrumentation et l'export OTLP, le service apparaît automatiquement
dans le filtre et le tableau **Applications détectées dans Prometheus** de
Grafana. Slack est un destinataire global des alertes configuré par webhook,
pas une connexion OTLP séparée pour chaque application.

Le portail publie aussi dans OpenTelemetry la liste des fiches actives. Prometheus
la collecte automatiquement; la variable **Application** et le dashboard de
supervision affichent les fiches actives immédiatement, y compris celles dont
le SDK n'est pas encore raccordé. Une nouvelle
fiche sans télémétrie déclenche l'alerte
`RegisteredApplicationWithoutTelemetry` après deux minutes; Alertmanager
l'envoie vers Slack lorsque `SLACK_WEBHOOK_URL` est configuré. Dès que le
service exporte ses signaux avec le `service.name` de la fiche, les panneaux
existants le prennent automatiquement en compte. L'ajout d'une fiche met donc
automatiquement à jour le registre Prometheus et les vues Grafana; seuls
l'installation du SDK dans l'application et la configuration initiale du
webhook Slack restent des opérations propres à l'environnement.

## Fonctions complémentaires du registre

Le registre peut désormais être filtré selon l'état de supervision et exporté
en CSV avec les critères de recherche, de période, d'archivage, de responsable
et de santé sélectionnés. Chaque rôle ne reçoit que les fiches qu'il est déjà
autorisé à consulter; l'export neutralise également les cellules susceptibles
d'être interprétées comme des formules par un tableur.

### Tests d'intégration du portail

```powershell
docker build -f demo-app\Dockerfile.test -t stagiaires-portal-tests demo-app
docker run --rm stagiaires-portal-tests
```

Les tests utilisent une base SQLite temporaire et ne touchent pas aux données du
volume `portal_data`.

## Phase 2 : dashboard et alertes

Ouvrez le dashboard réutilisable **Suivi des applications** dans le dossier
**Applications** de Grafana. Le filtre **Application** permet de sélectionner
un ou plusieurs services OpenTelemetry. Il présente le dernier battement de
coeur, les réponses HTTP par code, le débit, la latence globale p95, les 15
routes les plus appelées, les 15 routes les plus lentes et les journaux Loki de
l'application sélectionnée. Des panneaux dédiés montrent aussi le débit et la
latence p95 des requêtes SQL ainsi que la profondeur et la durée p95 des files
de travaux, lorsqu'une application exporte ces métriques.

Les métriques HTTP viennent des compteurs `stagiaires_app_requests_total` et
des histogrammes `stagiaires_app_request_duration_milliseconds_bucket`.
L'API de détection de fraude exporte le compteur
`stagiaires_db_queries_total` et l'histogramme
`stagiaires_db_query_duration_seconds_bucket`, avec l'opération SQL et le
système de base comme seules étiquettes; le texte SQL et ses paramètres ne
sont jamais ajoutés à ces métriques. Les autres applications doivent
instrumenter leur client de base avec les mêmes métriques pour apparaître dans
ces panneaux. Les panneaux
de travaux attendent `stagiaires_job_queue_depth` et
`stagiaires_job_duration_seconds_bucket`. Une absence de données signifie que
le service n'émet pas ces mesures; la pile actuelle n'intègre pas de système
de files de travaux.

Les connexions SSH, la réparation automatique et le déploiement parallèle de
serveurs ne sont pas activés par ce dashboard. Ils nécessitent des hôtes/agents
enregistrés, une politique d'autorisation et un gestionnaire sécurisé de clés;
le portail ne doit pas exécuter des commandes distantes arbitraires ni stocker
des clés privées en clair.

Les règles sont provisionnées dans `prometheus-alerts.yml` et évaluées par
Prometheus, puis transmises par Alertmanager. Le dashboard **Supervision de la
plateforme** ajoute une vue des alertes actives, des composants disponibles,
des battements des applications, de la latence p95, du taux de réponses 5xx et
des applications détectées automatiquement. Les alertes actives et les services
détectés y sont consultables dans des tableaux dédiés.

- **ApplicationWithoutHeartbeat** : aucun battement de coeur depuis 5 minutes,
  puis confirmé pendant 1 minute.
- **RegisteredApplicationWithoutTelemetry** : une application active du registre
  n'a pas encore émis de battement 2 minutes après son enregistrement.
- **ApplicationErrorRateHigh** : plus de 5 % de réponses 5xx sur 5 minutes,
  maintenu pendant 2 minutes.
- **ApplicationLatencyHigh** : latence p95 supérieure à 1 seconde, maintenue
  pendant 2 minutes.
- **ObservabilityComponentDown** : Collector, Tempo, Loki, Grafana ou Alertmanager
  inaccessible à Prometheus pendant 1 minute.

Le seuil de latence de 1 seconde est une valeur initiale à ajuster pour
l'entreprise. Les règles sont visibles dans Prometheus et dans la page
**Alerting** de Grafana. Alertmanager regroupe les alertes et envoie les
notifications Slack à leur déclenchement puis à leur résolution.

### Configuration Slack

Créez un webhook entrant Slack dans le canal destiné aux alertes, puis
enregistrez son URL uniquement dans le fichier `.env` local :

```dotenv
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

Ne partagez pas et ne versionnez pas cette URL : elle donne le droit d'écrire
dans le canal associé. Compose la transmet à Alertmanager comme secret monté
en fichier, sans l'inscrire dans `alertmanager.yml`. Après l'avoir ajoutée ou
modifiée, recréez les services concernés :

```powershell
docker compose up -d --force-recreate alertmanager prometheus
docker compose ps
docker compose logs --since 5m alertmanager
```

Ouvrez ensuite **Dashboards > Applications > Supervision de la plateforme**
pour suivre les alertes et les composants. Si `SLACK_WEBHOOK_URL` est vide,
aucune notification Slack n'est envoyée. Les e-mails de notification ne sont
pas configurés.

Le dashboard **Suivi des applications** comporte aussi les fenêtres **Accès
Prometheus** et **Accès Slack**, avec les accès Prometheus et Slack en haut du
tableau de bord. Ces liens s'ouvrent dans un nouvel onglet pour éviter que le
navigateur ne tente d'afficher ces sites dans le cadre Grafana intégré.
Prometheus utilise le même hôte que le portail; Slack ouvre son interface
officielle et nécessite une connexion ainsi qu'un espace de travail valide.
L'accès Slack et l'envoi des notifications dépendent de la disponibilité de
Slack et de la configuration du webhook dans `.env`.

Les routes suivantes simulent des cas de test sur l'application de démonstration
uniquement :

- `http://localhost:8080/demo/error` produit une réponse HTTP 500.
- `http://localhost:8080/demo/slow?seconds=1.2` simule une requête lente
  (durée plafonnée à 5 secondes).

Après toute modification de configuration, appliquez la phase 2 avec :

```powershell
docker compose up -d --build
docker compose ps
```

Les règles devraient apparaître dans Prometheus après le redémarrage. Dans
Grafana, ouvrez **Dashboards > Applications > Suivi des applications**.

## Arrêt et données

```powershell
docker compose down
```

Les données restent dans les volumes Docker nommés. La suppression des volumes
efface les traces, métriques, journaux et données Grafana ; ne l'effectuez que
si vous souhaitez réellement réinitialiser cette installation.

## Accès réseau local

Par défaut, le site et les ports OTLP sont liés à `127.0.0.1` pour éviter une
exposition involontaire. Pour rendre le site accessible aux autres machines du
réseau, modifiez la publication du port Caddy dans `docker-compose.yml` puis
limitez l'accès au réseau interne avec le pare-feu. N'exposez jamais Grafana ni
les ports OTLP directement à Internet. Les ports du Collector restent liés à
la boucle locale dans cette première configuration.

## Réinitialisation de la pile

La configuration est montée en lecture seule depuis les fichiers du projet.
Après une modification :

```powershell
docker compose up -d
docker compose ps
```

Pour examiner un service : `docker compose logs --tail 100 <service>`.
