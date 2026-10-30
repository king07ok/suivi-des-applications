import csv
import asyncio
import io
import re
import smtplib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DatabaseSession
from fastapi.testclient import TestClient

from portal.app import create_app, registered_application_observations
from portal.email import EmailSettings, send_email
from portal.models import Application, ApplicationCode, Base, User
from portal.security import hash_password, verify_password
from portal.web import fetch_application_health, health_snapshot


ADMIN_EMAIL = "admin@example.local"
ADMIN_PASSWORD = "Admin-password-2026!"
INTERN_PASSWORD = "Intern-password-2026!"
READER_PASSWORD = "Reader-password-2026!"


class PortalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "portal-test.db"
        self.app = create_app(
            database_url=f"sqlite:///{database_path}",
            session_secret_key="test-session-secret-key-with-more-than-32-bytes",
            initial_admin={
                "name": "Admin de test",
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD,
            },
        )
        self.client = TestClient(self.app)
        self.client.__enter__()
        health_patcher = patch(
            "portal.web.fetch_application_health",
            new=AsyncMock(
                side_effect=lambda service_names, prometheus_url: {
                    name: health_snapshot("no-data")
                    for name in service_names
                }
            ),
        )
        health_patcher.start()
        self.addCleanup(health_patcher.stop)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp_dir.cleanup()

    @staticmethod
    def csrf(response) -> str:
        match = re.search(
            r'name="csrf_token"\s+value="([^"]+)"',
            response.text,
        )
        if match is None:
            raise AssertionError("Page does not contain a CSRF token.")
        return match.group(1)

    def login(
        self,
        client=None,
        email=ADMIN_EMAIL,
        password=ADMIN_PASSWORD,
        follow_redirects=True,
    ):
        client = client or self.client
        page = client.get("/login")
        response = client.post(
            "/login",
            data={
                "csrf_token": self.csrf(page),
                "email": email,
                "password": password,
            },
            follow_redirects=follow_redirects,
        )
        return response

    def create_user(self, name, email, role, password):
        page = self.client.get("/users/new")
        return self.client.post(
            "/users/new",
            data={
                "csrf_token": self.csrf(page),
                "name": name,
                "email": email,
                "role": role,
                "password": password,
            },
        )

    def create_application(
        self,
        service_name="test-api",
        name="API de test",
        owner_id=None,
    ):
        page = self.client.get("/applications/new")
        if owner_id is None:
            with self.app.state.session_factory() as session:
                owner_id = session.query(User.id).filter(User.role == "moniteur").scalar()
        return self.client.post(
            "/applications/new",
            data={
                "csrf_token": self.csrf(page),
                "name": name,
                "service_name": service_name,
                "description": "Application de test fonctionnel.",
                "language": "Python",
                "repository_url": "https://git.example.local/team/test-api",
                "version": "1.2.3",
                "internship_start": "2026-01-01",
                "internship_end": "2026-12-31",
                "owner_id": str(owner_id),
            },
        )

    def test_account_creation_sends_welcome_email_without_password(self):
        self.assertEqual(self.login().status_code, 200)
        self.app.state.email_settings = EmailSettings(
            host="smtp.example.test",
            port=587,
            username="portal@example.test",
            password="smtp-secret",
            sender="portal@example.test",
            use_starttls=True,
            portal_url="https://portal.example.test",
        )
        initial_password = "Temporary-password-2026!"
        with patch("portal.web.send_email") as send:
            response = self.create_user(
                "Stagiaire e-mail",
                "stagiaire-mail@example.local",
                "moniteur",
                initial_password,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("E-mail de bienvenue envoyé", response.text)
        send.assert_called_once()
        settings, recipient, subject, body = send.call_args.args
        self.assertEqual(settings.host, "smtp.example.test")
        self.assertEqual(recipient, "stagiaire-mail@example.local")
        self.assertIn("bienvenue", subject.lower())
        self.assertIn("https://portal.example.test", body)
        self.assertNotIn(initial_password, body)

    def test_account_creation_reports_when_email_is_not_configured(self):
        self.assertEqual(self.login().status_code, 200)
        response = self.create_user(
            "Stagiaire sans SMTP",
            "stagiaire-sans-smtp@example.local",
            "moniteur",
            "Temporary-password-2026!",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("serveur SMTP n’est pas configuré", response.text)

    def test_smtp_failure_is_reported_without_rolling_back_account(self):
        self.assertEqual(self.login().status_code, 200)
        self.app.state.email_settings = EmailSettings(
            host="smtp.example.test",
            port=587,
            username="portal@example.test",
            password="smtp-secret",
            sender="portal@example.test",
            use_starttls=True,
            portal_url="https://portal.example.test",
        )
        with patch(
            "portal.web.send_email",
            side_effect=smtplib.SMTPException("SMTP unavailable"),
        ):
            response = self.create_user(
                "Stagiaire SMTP indisponible",
                "stagiaire-smtp-ko@example.local",
                "moniteur",
                "Temporary-password-2026!",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("n’a pas pu être envoyé", response.text)
        with self.app.state.session_factory() as session:
            self.assertIsNotNone(
                session.query(User)
                .filter(User.email == "stagiaire-smtp-ko@example.local")
                .one_or_none()
            )

    def test_smtp_sender_uses_starttls_and_authentication(self):
        settings = EmailSettings(
            host="smtp.example.test",
            port=587,
            username="portal@example.test",
            password="smtp-secret",
            sender="portal@example.test",
            use_starttls=True,
            portal_url="https://portal.example.test",
        )
        with patch("portal.email.smtplib.SMTP") as smtp:
            send_email(settings, "recipient@example.test", "Sujet", "Contenu")

        smtp.assert_called_once_with("smtp.example.test", 587, timeout=10)
        server = smtp.return_value.__enter__.return_value
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("portal@example.test", "smtp-secret")
        server.send_message.assert_called_once()

    def prepare_accounts_and_application(self):
        self.assertEqual(self.login().status_code, 200)
        intern = self.create_user(
            "Stagiaire A",
            "stagiaire-a@example.local",
            "moniteur",
            INTERN_PASSWORD,
        )
        self.assertEqual(intern.status_code, 200)
        reader = self.create_user(
            "Lecteur",
            "lecteur@example.local",
            "checker",
            READER_PASSWORD,
        )
        self.assertEqual(reader.status_code, 200)
        with self.app.state.session_factory() as session:
            owner_id = (
                session.query(User.id)
                .filter(User.email == "stagiaire-a@example.local")
                .scalar()
            )
        created = self.create_application(owner_id=owner_id)
        self.assertEqual(created.status_code, 200)
        with self.app.state.session_factory() as session:
            application_id = (
                session.query(Application.id)
                .filter(Application.service_name == "test-api")
                .scalar()
            )
        return owner_id, application_id

    def test_login_csrf_and_secure_session(self):
        page = self.client.get("/login")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Connexion", page.text)
        self.assertEqual(self.client.get("/applications/export.csv").status_code, 401)
        self.assertEqual(
            self.client.post(
                "/login",
                data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.login(password="incorrect-password").status_code,
            400,
        )
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Applications suivies", response.text)
        cookie = self.client.cookies.get("stagiaires_session")
        self.assertIsNotNone(cookie)

    def test_admin_creates_application_and_history_records_changes(self):
        self.assertEqual(self.login().status_code, 200)
        self.create_user(
            "Stagiaire A",
            "stagiaire-a@example.local",
            "moniteur",
            INTERN_PASSWORD,
        )
        with self.app.state.session_factory() as session:
            owner_id = (
                session.query(User.id)
                .filter(User.email == "stagiaire-a@example.local")
                .scalar()
            )
        create_response = self.create_application(owner_id=owner_id)
        self.assertEqual(create_response.status_code, 200)
        self.assertIn("Application enregistrée", create_response.text)
        detail = self.client.get("/applications/1")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("test-api", detail.text)
        self.assertIn("Fiche créée", detail.text)
        self.assertIn('data-health-status="no-data"', detail.text)
        self.assertIn("/grafana/d/stagiaires-applications/suivi-des-applications", detail.text)
        self.assertIn("OTEL_SERVICE_NAME=test-api", detail.text)
        self.assertIn(
            "OTEL_EXPORTER_OTLP_ENDPOINT=http://portal-otel-gateway:4317",
            detail.text,
        )
        self.assertIn("OTEL_METRICS_EXPORTER=otlp", detail.text)
        self.assertIn("OTEL_LOGS_EXPORTER=otlp", detail.text)
        self.assertIn("ne peut pas modifier le code ni l’environnement", detail.text)
        self.assertIn("Vérifier le raccordement", detail.text)
        observations = registered_application_observations(
            self.app.state.session_factory
        )
        self.assertIn(
            {
                "value": 1,
                "attributes": {"application_service_name": "test-api"},
            },
            [
                {
                    "value": observation.value,
                    "attributes": dict(observation.attributes),
                }
                for observation in observations
            ],
        )
        health = self.client.get("/applications/1/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "no-data")

        edit_page = self.client.get("/applications/1/edit")
        edit = self.client.post(
            "/applications/1/edit",
            data={
                "csrf_token": self.csrf(edit_page),
                "name": "API de test mise à jour",
                "service_name": "test-api",
                "description": "Description mise à jour.",
                "language": "Python",
                "repository_url": "https://git.example.local/team/test-api",
                "version": "2.0.0",
                "internship_start": "2026-01-01",
                "internship_end": "2026-12-31",
                "owner_id": str(owner_id),
            },
        )
        self.assertEqual(edit.status_code, 200)
        updated = self.client.get("/applications/1")
        self.assertIn("API de test mise à jour", updated.text)
        self.assertIn("Fiche modifiée", updated.text)

    def test_telemetry_check_reports_signals_and_requires_csrf(self):
        self.assertEqual(self.login().status_code, 200)
        self.create_user(
            "Stagiaire A",
            "stagiaire-a@example.local",
            "moniteur",
            INTERN_PASSWORD,
        )
        with self.app.state.session_factory() as session:
            owner_id = (
                session.query(User.id)
                .filter(User.email == "stagiaire-a@example.local")
                .scalar()
            )
        self.assertEqual(self.create_application(owner_id=owner_id).status_code, 200)
        self.app.state.slack_alerts_enabled = True
        check_url = "/applications/1/telemetry/check"
        self.assertEqual(self.client.post(check_url).status_code, 403)

        with patch(
            "portal.web.fetch_application_telemetry_status",
            new=AsyncMock(
                return_value={
                    "status": "connected",
                    "http": True,
                    "heartbeat": True,
                    "sql": False,
                    "jobs": False,
                }
            ),
        ) as telemetry_check:
            response = self.client.post(
                check_url,
                data={"csrf_token": self.csrf(self.client.get("/applications/1"))},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("métriques HTTP reçues", response.text)
        self.assertIn("battement reçu", response.text)
        self.assertIn("métriques SQL absentes", response.text)
        self.assertIn("Alertes Slack configurées au niveau de la plateforme", response.text)
        telemetry_check.assert_awaited_once_with(
            "test-api",
            self.app.state.prometheus_url,
        )

    def test_code_studio_saves_site_code_and_isolates_preview(self):
        self.assertEqual(self.login().status_code, 200)
        self.create_user(
            "Stagiaire A",
            "stagiaire-a@example.local",
            "moniteur",
            INTERN_PASSWORD,
        )
        with self.app.state.session_factory() as session:
            owner_id = (
                session.query(User.id)
                .filter(User.email == "stagiaire-a@example.local")
                .scalar()
            )
        self.assertEqual(self.create_application(owner_id=owner_id).status_code, 200)

        detail = self.client.get("/applications/1")
        self.assertIn('href="/applications/1/studio"', detail.text)
        studio = self.client.get("/applications/1/studio")
        self.assertEqual(studio.status_code, 200)
        self.assertIn('sandbox="allow-scripts"', studio.text)
        self.assertIn("ne modifie pas le", studio.text)
        csrf = self.csrf(studio)
        source = {
            "html_code": '<main><h1>Édition du site test</h1><p id="result"></p></main>',
            "css_code": "body { color: #234; }",
            "js_code": 'document.querySelector("#result").textContent = "Aperçu actif";',
        }
        saved = self.client.post(
            "/applications/1/studio",
            data={"csrf_token": csrf, "revision": "0", **source},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertIn("Version 1 enregistrée", saved.text)

        preview = self.client.get("/applications/1/preview")
        self.assertEqual(preview.status_code, 200)
        self.assertIn(source["html_code"], preview.text)
        self.assertIn(source["css_code"], preview.text)
        self.assertIn(source["js_code"], preview.text)
        policy = preview.headers["content-security-policy"]
        self.assertIn("sandbox allow-scripts", policy)
        self.assertIn("connect-src 'none'", policy)
        self.assertIn("script-src 'unsafe-inline'", policy)
        self.assertIn("frame-ancestors 'self'", policy)

        with self.app.state.session_factory() as session:
            saved_code = (
                session.query(ApplicationCode)
                .filter_by(application_id=1)
                .one()
            )
            self.assertEqual(saved_code.revision, 1)
            self.assertEqual(saved_code.updated_by_id, 1)

        stale = self.client.post(
            "/applications/1/studio",
            data={
                "csrf_token": csrf,
                "revision": "0",
                **{**source, "html_code": "<h1>Ancienne version</h1>"},
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(
            re.search(r'name="revision"\s+value="([^"]+)"', stale.text).group(1),
            "0",
        )
        repeated_stale = self.client.post(
            "/applications/1/studio",
            data={
                "csrf_token": csrf,
                "revision": "0",
                **{**source, "html_code": "<h1>Ancienne version</h1>"},
            },
        )
        self.assertEqual(repeated_stale.status_code, 409)
        with self.app.state.session_factory() as session:
            saved_code = session.query(ApplicationCode).filter_by(application_id=1).one()
            self.assertEqual(saved_code.revision, 1)
            self.assertEqual(saved_code.html_code, source["html_code"])

        oversized = self.client.post(
            "/applications/1/studio",
            data={
                "csrf_token": csrf,
                "revision": "1",
                **{**source, "html_code": "x" * 20001},
            },
        )
        self.assertEqual(oversized.status_code, 422)
        self.assertIn("20 000 caractères", oversized.text)
        with self.app.state.session_factory() as session:
            saved_code = session.query(ApplicationCode).filter_by(application_id=1).one()
            self.assertEqual(saved_code.revision, 1)
            self.assertEqual(saved_code.html_code, source["html_code"])

        missing_csrf = self.client.post(
            "/applications/1/studio",
            data={"revision": "1", **source},
        )
        self.assertEqual(missing_csrf.status_code, 403)

    def test_moniteur_can_edit_all_applications_and_checker_is_dashboard_only(self):
        owner_a, application_a = self.prepare_accounts_and_application()
        self.create_user(
            "Stagiaire B",
            "stagiaire-b@example.local",
            "moniteur",
            "Intern-B-password-2026!",
        )
        with self.app.state.session_factory() as session:
            owner_b = (
                session.query(User.id)
                .filter(User.email == "stagiaire-b@example.local")
                .scalar()
            )
        self.assertEqual(
            self.create_application(
                service_name="other-api",
                name="Autre API",
                owner_id=owner_b,
            ).status_code,
            200,
        )

        intern = TestClient(self.app)
        self.addCleanup(intern.close)
        self.assertEqual(
            self.login(
                intern,
                "stagiaire-a@example.local",
                INTERN_PASSWORD,
            ).status_code,
            200,
        )
        self.assertEqual(intern.get(f"/applications/{application_a}/studio").status_code, 200)
        self.assertEqual(intern.get("/applications/2/studio").status_code, 200)
        self.assertEqual(intern.get("/applications/2/preview").status_code, 200)
        self.assertEqual(intern.get("/users").status_code, 403)
        self.assertEqual(intern.get("/users/new").status_code, 403)

        reader = TestClient(self.app)
        self.addCleanup(reader.close)
        response = self.login(
            reader,
            "lecteur@example.local",
            READER_PASSWORD,
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            "/grafana/d/stagiaires-applications/suivi-des-applications",
        )
        self.assertEqual(reader.get("/applications").status_code, 403)
        self.assertEqual(reader.get("/applications/export.csv").status_code, 403)
        grafana_auth = reader.get("/auth/grafana")
        self.assertEqual(grafana_auth.status_code, 200)
        self.assertRegex(grafana_auth.headers["x-webauth-user"], r"^portal-user-\d+$")
        self.assertEqual(reader.get("/users").status_code, 403)
        self.assertEqual(owner_a, 2)

    def test_moniteur_can_view_and_edit_all_applications(self):
        owner_a, application_a = self.prepare_accounts_and_application()
        self.create_user(
            "Stagiaire B",
            "stagiaire-b@example.local",
            "moniteur",
            "Intern-B-password-2026!",
        )
        with self.app.state.session_factory() as session:
            owner_b = (
                session.query(User.id)
                .filter(User.email == "stagiaire-b@example.local")
                .scalar()
            )
        second = self.create_application(
            service_name="other-api",
            name="Autre API",
            owner_id=owner_b,
        )
        self.assertEqual(second.status_code, 200)

        intern_client = TestClient(self.app)
        self.addCleanup(intern_client.close)
        self.assertEqual(
            self.login(
                intern_client,
                "stagiaire-a@example.local",
                INTERN_PASSWORD,
            ).status_code,
            200,
        )
        visible = intern_client.get("/applications")
        self.assertIn("test-api", visible.text)
        self.assertIn("other-api", visible.text)
        self.assertIn("/applications/export.csv?", visible.text)
        self.assertIn('name="owner_id"', visible.text)
        self.assertEqual(
            intern_client.get("/applications/export.csv?status=all").status_code,
            200,
        )
        self.assertEqual(intern_client.get(f"/applications/{application_a}").status_code, 200)
        self.assertEqual(intern_client.get("/applications/2").status_code, 200)
        self.assertEqual(intern_client.get("/applications/2/health").status_code, 200)
        self.assertEqual(intern_client.get("/applications/2/edit").status_code, 200)
        self.assertEqual(owner_a, 2)

    def test_registered_application_metric_excludes_archived_applications(self):
        self.prepare_accounts_and_application()
        active = registered_application_observations(
            self.app.state.session_factory
        )
        self.assertIn(
            "test-api",
            {
                observation.attributes["application_service_name"]
                for observation in active
            },
        )
        with self.app.state.session_factory() as session:
            application = (
                session.query(Application)
                .filter_by(service_name="test-api")
                .one()
            )
            application.archived = True
            session.commit()

        archived = registered_application_observations(
            self.app.state.session_factory
        )
        self.assertNotIn(
            "test-api",
            {
                observation.attributes["application_service_name"]
                for observation in archived
            },
        )

    def test_portal_handles_fifty_simulated_applications(self):
        self.assertEqual(self.login().status_code, 200)
        intern = self.create_user(
            "Stagiaire simulation",
            "simulation@example.local",
            "moniteur",
            INTERN_PASSWORD,
        )
        self.assertEqual(intern.status_code, 200)
        with self.app.state.session_factory() as session:
            owner_id = (
                session.query(User.id)
                .filter(User.email == "simulation@example.local")
                .scalar()
            )

        for number in range(1, 51):
            response = self.create_application(
                service_name=f"simulation-site-{number:02d}",
                name=f"Site simulé {number:02d}",
                owner_id=owner_id,
            )
            self.assertEqual(
                response.status_code,
                200,
                f"Failed to create simulated site {number}: {response.text}",
            )

        listing = self.client.get("/applications")
        self.assertEqual(listing.status_code, 200)
        for number in range(1, 51):
            self.assertIn(f"simulation-site-{number:02d}", listing.text)

        filtered = self.client.get("/applications?q=simulation-site-25")
        self.assertEqual(filtered.status_code, 200)
        self.assertIn("simulation-site-25", filtered.text)
        self.assertNotIn("simulation-site-24", filtered.text)
        self.assertNotIn("simulation-site-26", filtered.text)

        simulated_health = AsyncMock(
            side_effect=lambda service_names, prometheus_url: {
                service_name: health_snapshot("healthy", 20)
                for service_name in service_names
            }
        )
        with patch(
            "portal.web.fetch_application_health",
            new=simulated_health,
        ):
            healthy = self.client.get("/applications?health=healthy")
            self.assertEqual(healthy.status_code, 200)
            for number in range(1, 51):
                self.assertIn(f"simulation-site-{number:02d}", healthy.text)
            self.assertEqual(
                len(simulated_health.await_args.args[0]),
                50,
            )
            detail = self.client.get("/applications/50")
            self.assertEqual(detail.status_code, 200)
            self.assertIn("OTEL_SERVICE_NAME=simulation-site-50", detail.text)

        export = self.client.get("/applications/export.csv")
        self.assertEqual(export.status_code, 200)
        rows = list(
            csv.reader(
                io.StringIO(export.content.decode("utf-8-sig")),
                delimiter=";",
            )
        )
        self.assertEqual(len(rows), 51)
        self.assertEqual(
            {row[1] for row in rows[1:]},
            {f"simulation-site-{number:02d}" for number in range(1, 51)},
        )

        observations = registered_application_observations(
            self.app.state.session_factory
        )
        self.assertEqual(len(observations), 50)
        self.assertEqual(
            {
                observation.attributes["application_service_name"]
                for observation in observations
            },
            {f"simulation-site-{number:02d}" for number in range(1, 51)},
        )

    def test_checker_cannot_access_portal_or_account_management(self):
        self.prepare_accounts_and_application()
        reader = TestClient(self.app)
        self.addCleanup(reader.close)
        unauthenticated = TestClient(self.app)
        self.addCleanup(unauthenticated.close)
        auth_redirect = unauthenticated.get("/auth/grafana", follow_redirects=False)
        self.assertEqual(auth_redirect.status_code, 303)
        self.assertEqual(auth_redirect.headers["location"], "/login")
        self.assertEqual(
            self.login(
                reader,
                "lecteur@example.local",
                READER_PASSWORD,
                follow_redirects=False,
            ).status_code,
            303,
        )
        self.assertEqual(
            reader.get("/", follow_redirects=False).headers["location"],
            "/grafana/d/stagiaires-applications/suivi-des-applications",
        )
        self.assertEqual(reader.get("/applications").status_code, 403)
        self.assertEqual(reader.get("/applications/new").status_code, 403)
        self.assertEqual(reader.get("/applications/export.csv").status_code, 403)
        self.assertEqual(reader.get("/account/password").status_code, 403)
        self.assertEqual(reader.get("/users").status_code, 403)
        self.assertEqual(reader.get("/users/new").status_code, 403)

    def test_health_filter_and_csv_export_respect_scope_and_escape_formulas(self):
        owner_id, _ = self.prepare_accounts_and_application()
        self.create_user(
            "Stagiaire B",
            "stagiaire-b@example.local",
            "moniteur",
            "Intern-B-password-2026!",
        )
        with self.app.state.session_factory() as session:
            owner_b = (
                session.query(User.id)
                .filter(User.email == "stagiaire-b@example.local")
                .scalar()
            )
            application = session.query(Application).filter_by(service_name="test-api").one()
            application.name = '=HYPERLINK("https://example.invalid")'
            session.commit()
        self.assertEqual(
            self.create_application(
                service_name="healthy-api",
                name="API opérationnelle",
                owner_id=owner_b,
            ).status_code,
            200,
        )
        with patch(
            "portal.web.fetch_application_health",
            new=AsyncMock(
                return_value={
                    "test-api": health_snapshot("no-data"),
                    "healthy-api": health_snapshot("healthy", 12),
                }
            ),
        ):
            filtered = self.client.get("/applications?health=healthy")
            self.assertIn("healthy-api", filtered.text)
            self.assertNotIn("test-api", filtered.text)
            self.assertIn("health=healthy", filtered.text)

            export = self.client.get("/applications/export.csv?health=healthy")
            self.assertEqual(export.status_code, 200)
            self.assertIn("text/csv", export.headers["content-type"])
            self.assertIn("attachment;", export.headers["content-disposition"])
            rows = list(
                csv.reader(
                    io.StringIO(export.content.decode("utf-8-sig")),
                    delimiter=";",
                )
            )
            self.assertEqual(rows[0][0], "Application")
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1][1], "healthy-api")

            all_export = self.client.get("/applications/export.csv")
            all_rows = list(
                csv.reader(
                    io.StringIO(all_export.content.decode("utf-8-sig")),
                    delimiter=";",
                )
            )
            risky_row = next(row for row in all_rows[1:] if row[1] == "test-api")
            self.assertTrue(risky_row[0].startswith("'=HYPERLINK("))
            self.assertEqual(len(all_rows), 3)

            intern = TestClient(self.app)
            self.addCleanup(intern.close)
            self.assertEqual(
                self.login(
                    intern,
                    "stagiaire-a@example.local",
                    INTERN_PASSWORD,
                ).status_code,
                200,
            )
            scoped_export = intern.get("/applications/export.csv?status=all")
            scoped_rows = list(
                csv.reader(
                    io.StringIO(scoped_export.content.decode("utf-8-sig")),
                    delimiter=";",
                )
            )
            self.assertEqual(len(scoped_rows), 3)
            self.assertEqual(
                {row[1] for row in scoped_rows[1:]},
                {"test-api", "healthy-api"},
            )

    def test_csrf_validation_duplicate_service_and_archival(self):
        self.prepare_accounts_and_application()
        page = self.client.get("/applications/new")
        duplicate = self.client.post(
            "/applications/new",
            data={
                "csrf_token": self.csrf(page),
                "name": "Copie",
                "service_name": "test-api",
                "owner_id": "2",
            },
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("déjà utilisé", duplicate.text)

        detail = self.client.get("/applications/1")
        self.assertEqual(
            self.client.post(
                "/applications/1/archive",
                data={"csrf_token": "invalid"},
            ).status_code,
            403,
        )
        archived = self.client.post(
            "/applications/1/archive",
            data={"csrf_token": self.csrf(detail)},
        )
        self.assertEqual(archived.status_code, 200)
        self.assertNotIn("test-api", self.client.get("/applications").text)
        self.assertIn("test-api", self.client.get("/applications?status=archived").text)
        self.assertIn("Application archivée", self.client.get("/applications/1").text)

    def test_password_change_and_account_deactivation(self):
        self.assertEqual(self.login().status_code, 200)
        self.create_user(
            "Stagiaire à désactiver",
            "inactive@example.local",
            "moniteur",
            "Inactive-password-2026!",
        )
        self.app.state.email_settings = EmailSettings(
            host="smtp.example.test",
            port=587,
            username="portal@example.test",
            password="smtp-secret",
            sender="portal@example.test",
            use_starttls=True,
            portal_url="https://portal.example.test",
        )
        password_page = self.client.get("/account/password")
        new_password = "Admin-password-updated-2026!"
        with patch("portal.web.send_email") as send:
            changed = self.client.post(
                "/account/password",
                data={
                    "csrf_token": self.csrf(password_page),
                    "current_password": ADMIN_PASSWORD,
                    "new_password": new_password,
                    "confirm_password": new_password,
                },
            )
        self.assertEqual(changed.status_code, 200)
        self.assertIn("Avis de sécurité envoyé par e-mail", changed.text)
        send.assert_called_once()
        body = send.call_args.args[3]
        self.assertIn("vient d’être modifié", body)
        self.assertNotIn(new_password, body)
        with self.app.state.session_factory() as session:
            admin = session.query(User).filter(User.email == ADMIN_EMAIL).one()
            self.assertTrue(verify_password(admin.password_hash, "Admin-password-updated-2026!"))
            inactive_id = session.query(User.id).filter(User.email == "inactive@example.local").scalar()
        csrf = self.csrf(self.client.get("/users"))
        self.client.post(
            f"/users/{inactive_id}/toggle",
            data={"csrf_token": csrf},
        )
        with self.app.state.session_factory() as session:
            inactive = session.get(User, inactive_id)
            self.assertFalse(inactive.is_active)

        deactivated_client = TestClient(self.app)
        self.addCleanup(deactivated_client.close)
        response = self.login(
            deactivated_client,
            "inactive@example.local",
            "Inactive-password-2026!",
        )
        self.assertEqual(response.status_code, 400)

    def test_startup_migrates_legacy_roles(self):
        database_path = Path(self.temp_dir.name) / "legacy-roles.db"
        app = create_app(
            database_url=f"sqlite:///{database_path}",
            session_secret_key="test-session-secret-key-with-more-than-32-bytes",
            initial_admin={
                "name": "Admin de migration",
                "email": "migration-admin@example.local",
                "password": ADMIN_PASSWORD,
            },
        )
        engine = create_engine(f"sqlite:///{database_path}")
        Base.metadata.create_all(engine)
        with DatabaseSession(engine) as session:
            session.add_all(
                [
                    User(
                        name="Ancien stagiaire",
                        email="legacy-monitor@example.local",
                        password_hash=hash_password(INTERN_PASSWORD),
                        role="stagiaire",
                    ),
                    User(
                        name="Ancien lecteur",
                        email="legacy-checker@example.local",
                        password_hash=hash_password(READER_PASSWORD),
                        role="lecteur",
                    ),
                ]
            )
            session.commit()
        engine.dispose()

        with TestClient(app):
            with app.state.session_factory() as session:
                self.assertEqual(
                    session.query(User.role)
                    .filter(User.email == "legacy-monitor@example.local")
                    .scalar(),
                    "moniteur",
                )
                self.assertEqual(
                    session.query(User.role)
                    .filter(User.email == "legacy-checker@example.local")
                    .scalar(),
                    "checker",
                )

    def test_prometheus_heartbeat_age_maps_to_health_states(self):
        samples = [
            {"metric": {"service_name": "healthy-api"}, "value": [0, "60"]},
            {"metric": {"service_name": "warning-api"}, "value": [0, "61"]},
            {"metric": {"service_name": "threshold-api"}, "value": [0, "300"]},
            {"metric": {"service_name": "down-api"}, "value": [0, "301"]},
        ]
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"status": "success", "data": {"resultType": "vector", "result": samples}},
            )
        )
        async_client = httpx.AsyncClient
        with patch(
            "portal.web.httpx.AsyncClient",
            side_effect=lambda **kwargs: async_client(
                transport=transport,
                **kwargs,
            ),
        ):
            health = asyncio.run(
                fetch_application_health(
                    [
                        "healthy-api",
                        "warning-api",
                        "threshold-api",
                        "down-api",
                        "new-api",
                    ],
                    "http://prometheus",
                )
            )
        self.assertEqual(health["healthy-api"]["status"], "healthy")
        self.assertEqual(health["warning-api"]["status"], "warning")
        self.assertEqual(health["threshold-api"]["status"], "warning")
        self.assertEqual(health["down-api"]["status"], "unavailable")
        self.assertEqual(health["new-api"]["status"], "no-data")

    def test_prometheus_failure_produces_explicit_unknown_health(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(503, text="temporarily unavailable")
        )
        async_client = httpx.AsyncClient
        with patch(
            "portal.web.httpx.AsyncClient",
            side_effect=lambda **kwargs: async_client(
                transport=transport,
                **kwargs,
            ),
        ):
            health = asyncio.run(
                fetch_application_health(["test-api"], "http://prometheus")
            )
        self.assertEqual(health["test-api"]["status"], "unknown")
        self.assertIn("Prometheus est indisponible", health["test-api"]["description"])


if __name__ == "__main__":
    unittest.main()
