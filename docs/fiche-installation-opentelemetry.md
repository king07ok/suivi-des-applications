# Fiche d'installation OpenTelemetry sur un serveur

## Objectif

Cette fiche explique comment envoyer les métriques et traces d'une application
vers la pile de supervision du portail. L'application utilise un SDK ou une
auto-instrumentation OpenTelemetry; le Collector partagé reçoit les données,
Prometheus stocke les métriques, Tempo les traces, Loki les journaux et Grafana
les visualise.

Il n'est normalement pas nécessaire d'installer un Collector distinct pour
chaque application. Il faut toutefois pouvoir modifier l'application ou
installer un agent/exporter compatible sur son serveur.

## Prérequis

- Accès au code, à l'environnement d'exécution ou au serveur de l'application.
- La pile du portail en fonctionnement, avec `otel-collector`, Prometheus,
  Tempo, Loki et Grafana.
- Un nom de service stable et unique, par exemple `paiement-api`, ainsi qu'un
  environnement (`production`, `recette` ou `local`).
- Un chemin réseau autorisé de l'application au Collector.
- Les droits nécessaires pour ajouter les dépendances de télémétrie et
  redémarrer l'application.

Les applications dont le code et l'environnement ne sont pas accessibles ne
peuvent pas recevoir une instrumentation OpenTelemetry interne. Pour celles-ci,
utiliser un exporter/API fourni par l'éditeur ou une sonde Blackbox; cette
dernière mesure surtout la disponibilité, la latence et les informations HTTP.

## Choisir l'adresse du Collector

L'endpoint dépend de l'emplacement de l'application :

| Emplacement de l'application | Endpoint OTLP/HTTP à utiliser |
| --- | --- |
| Conteneur sur le même réseau Docker Compose `telemetry` | `http://otel-collector:4318` |
| Processus lancé sur le même serveur que Docker | `http://127.0.0.1:4318` |
| Autre serveur | Adresse privée sécurisée du Collector; voir la section réseau |

Le Collector de cette pile reçoit OTLP en gRPC sur le port `4317` et en
HTTP/Protobuf sur le port `4318`. Les ports publiés sur l'hôte sont liés à
`127.0.0.1` par défaut. Un serveur distant ne peut donc pas les joindre sans
configuration réseau supplémentaire.

### Application dans un autre conteneur

Rattacher le service applicatif au même réseau Docker `telemetry` que le
Collector, puis configurer `http://otel-collector:4318`. Si l'application
utilise un autre fichier Compose, le réseau peut devoir être déclaré externe.
Vérifier son nom réel avec `docker network ls`; ne pas supposer qu'il est
identique entre les installations.

```yaml
services:
  mon-application:
    environment:
      OTEL_SERVICE_NAME: paiement-api
      OTEL_RESOURCE_ATTRIBUTES: service.namespace=entreprise,deployment.environment.name=production
      OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4318
      OTEL_EXPORTER_OTLP_PROTOCOL: http/protobuf
      OTEL_METRICS_EXPORTER: otlp
      OTEL_TRACES_EXPORTER: otlp
    networks:
      - telemetry

networks:
  telemetry:
    external: true
    name: remplacer-par-le-nom-reel-du-reseau
```

Si ce service est ajouté au fichier Compose principal, réutiliser son réseau
`telemetry` existant et ne pas le redéclarer comme réseau externe.

### Application installée directement sur le serveur

Si le processus tourne sur la même machine que Docker, le port local publié
peut être utilisé :

```dotenv
OTEL_SERVICE_NAME=paiement-api
OTEL_RESOURCE_ATTRIBUTES=service.namespace=entreprise,deployment.environment.name=production
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_METRICS_EXPORTER=otlp
OTEL_TRACES_EXPORTER=otlp
```

Définir ces variables dans la configuration du service (par exemple son
fichier systemd ou son fichier d'environnement), sans les écrire en dur dans
le code source. Redémarrer ensuite le service.

### Application sur un autre serveur

Ne pas exposer OTLP sans protection sur Internet. Préférer un VPN ou un réseau
privé; autoriser uniquement l'adresse du serveur applicatif dans le pare-feu,
et protéger le transport par TLS et authentification (par exemple avec un
proxy ou un Collector configuré à cet effet). Le port loopback par défaut
doit être adapté pour écouter sur une adresse privée; ne pas le remplacer par
une écoute publique `0.0.0.0` sans contrôle d'accès.

## Installer l'instrumentation

### Exemple pour Python

Dans l'environnement virtuel de l'application, ajouter les dépendances au
fichier de dépendances du projet et installer le SDK/exporter :

```powershell
python -m pip install opentelemetry-distro opentelemetry-exporter-otlp
opentelemetry-bootstrap -a install
```

La commande `opentelemetry-bootstrap` détecte des bibliothèques connues et
propose leurs instrumentations. Ajouter les paquets nécessaires au fichier de
dépendances et verrouiller leurs versions avant le déploiement.

Pour démarrer une application ASGI FastAPI avec auto-instrumentation, utiliser
le lanceur OpenTelemetry :

```powershell
opentelemetry-instrument python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Configurer les variables OTLP de la section précédente dans le service qui
lance cette commande. L'auto-instrumentation fournit les signaux standards
qu'elle sait observer; les métriques métier (par exemple la profondeur d'une
file ou le résultat d'un traitement) doivent être ajoutées dans le code avec
l'API OpenTelemetry.

Pour les autres langages, installer les SDK et exporters officiels adaptés
(Java, Node.js, .NET, Go, etc.) et suivre la procédure propre au framework.
L'endpoint, le nom de service et les attributs de ressource restent les mêmes.

## Choisir les métriques et attributs

Commencer par quelques mesures répondant à un besoin opérationnel :

- requêtes, erreurs et codes de réponse HTTP;
- durée des requêtes et opérations de base de données (histogrammes);
- tâches en attente, traitements terminés et échecs;
- appels vers des dépendances externes;
- ressources du processus, si elles ne sont pas déjà fournies par un exporter.

Utiliser des attributs bornés et stables : `service.name`, environnement,
méthode HTTP, code de statut, et modèle de route (`/commandes/{id}`). Éviter
les URL brutes, identifiants utilisateur/session, adresses e-mail, valeurs
SQL, jetons et tout autre secret. Les identifiants uniques ajoutés comme labels
créent une cardinalité élevée, augmentent le volume et peuvent atteindre les
limites de séries de Prometheus/Grafana Cloud.

## Déployer et vérifier

1. Ajouter les dépendances au projet, configurer les variables OTLP et déployer
   l'application avec le mécanisme habituel.
2. Vérifier la connexion réseau et les journaux :

   ```powershell
   docker compose ps otel-collector prometheus
   docker compose logs --since 5m otel-collector
   docker compose logs --since 5m mon-application
   ```

3. Générer quelques requêtes normales vers l'application et attendre au moins
   un cycle d'export.
4. Dans Grafana Explore, choisir Prometheus et rechercher une métrique de
   l'application. Dans cette pile, les métriques OpenTelemetry sont scrutées
   par Prometheus depuis `otel-collector:8889`; les noms peuvent être préfixés
   par `stagiaires_`. Par exemple :

   ```promql
   stagiaires_app_requests_total{service_name="paiement-api"}
   ```

5. Pour les traces, ouvrir Tempo et rechercher le service `paiement-api`.
   Pour les journaux, rechercher dans Loki avec `{service_name="paiement-api"}`
   si l'application exporte ses logs en OTLP.
6. Après confirmation des données dans Explore, créer ou adapter un tableau
   Grafana avec des requêtes visant les séries réellement reçues. Installer
   l'instrumentation ne crée pas automatiquement un tableau dédié.

Si les séries n'apparaissent pas, vérifier d'abord le `service.name`, le réseau
et l'endpoint, le protocole (`http/protobuf` pour le port `4318`), les
exporters activés et les journaux de l'application/Collector. Ne pas créer de
panneau avec des métriques supposées ou sans données.

## Cas où l'application expose déjà `/metrics`

Si l'application ou son exporter fournit déjà un endpoint Prometheus, il n'est
pas obligatoire de la réinstrumenter avec OpenTelemetry. Ajouter une cible de
scrape dans `prometheus.yml`, en vérifiant que le conteneur Prometheus peut
joindre l'adresse, le port et le chemin. Les métriques de l'hôte ou des
conteneurs peuvent aussi venir d'exporters spécialisés tels que Node Exporter
ou cAdvisor.

## Particularités de cette pile

- Le Collector OTLP partagé utilise les récepteurs `4317` et `4318`.
- Les métriques reçues sont exposées à Prometheus sur `otel-collector:8889`.
- Les traces sont exportées vers Tempo et les logs OTLP vers Loki.
- Le portail de démonstration montre déjà une intégration Python/FastAPI avec
  des métriques personnalisées dans `demo-app/main.py`.
- Les applications intégrées à Grafana ne partagent pas nécessairement leur
  méthode de collecte : Jenkins utilise son endpoint Prometheus, tandis que les
  sites non instrumentés sont suivis par Blackbox.
