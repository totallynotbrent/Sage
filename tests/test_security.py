from fastapi.testclient import TestClient

from app.main import create_app


def _secured_client(settings):
    sec = settings.model_copy(update={"sage_password": "Brent8"})
    return TestClient(create_app(sec))


def test_password_gate_redirects_unauthenticated_pages(settings):
    with _secured_client(settings) as c:
        for path in ["/", "/index.html", "/sage-workspace.html", "/dither.js", "/wallpaper.jpg"]:
            r = c.get(path, follow_redirects=False)
            assert r.status_code == 302, path
            assert r.headers["location"] == "/login", path


def test_password_gate_rejects_unauthenticated_api(settings):
    with _secured_client(settings) as c:
        r = c.get("/api/sessions", follow_redirects=False)
        assert r.status_code == 401


def test_login_and_health_assets_stay_open(settings):
    with _secured_client(settings) as c:
        assert c.get("/login", follow_redirects=False).status_code == 200
        assert c.get("/auth/dither.js").status_code == 200
        assert c.get("/auth/wallpaper.jpg").status_code == 200
        assert c.get("/api/health").status_code == 200


def test_login_wrong_password_stays_gated(settings):
    with _secured_client(settings) as c:
        r = c.post("/api/login", json={"password": "nope"})
        assert r.status_code == 401
        assert c.get("/", follow_redirects=False).status_code == 302


def test_login_success_opens_pages(settings):
    with _secured_client(settings) as c:
        r = c.post("/api/login", json={"password": "Brent8"})
        assert r.status_code == 200
        assert c.get("/", follow_redirects=False).status_code == 200
        assert c.get("/api/sessions").status_code == 200


def test_logout_re_gates(settings):
    with _secured_client(settings) as c:
        c.post("/api/login", json={"password": "Brent8"})
        assert c.get("/", follow_redirects=False).status_code == 200
        c.get("/api/logout")
        assert c.get("/", follow_redirects=False).status_code == 302