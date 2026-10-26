"""鉴权 API 测试（AC-1.1/1.2/1.3）。"""


def test_register_creates_default_subject(client):
    """AC-1.1 新邮箱注册成功后自动登录并创建默认 Subject（本人）。"""
    resp = client.post(
        "/api/auth/register",
        json={"email": "a1@example.com", "password": "password123",
              "displayName": "管理员"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["token"]
    assert data["account"]["email"] == "a1@example.com"
    assert data["defaultSubject"]["name"] == "本人"
    assert data["defaultSubject"]["isDefault"] is True


def test_register_duplicate_email_conflict(client):
    client.post("/api/auth/register",
                json={"email": "dup@example.com", "password": "password123"})
    resp = client.post("/api/auth/register",
                       json={"email": "dup@example.com", "password": "password123"})
    assert resp.status_code == 409
    assert resp.json()["code"] not in (0, None)


def test_login_wrong_password_401_unified(client):
    """AC-1.2 错误密码登录返回 401 且不泄露账号是否存在。"""
    client.post("/api/auth/register",
                json={"email": "b@example.com", "password": "password123"})
    wrong = client.post("/api/auth/login",
                        json={"email": "b@example.com", "password": "bad-pass"})
    missing = client.post("/api/auth/login",
                          json={"email": "nobody@example.com", "password": "bad-pass"})
    assert wrong.status_code == 401
    assert missing.status_code == 401
    assert wrong.json()["code"] == missing.json()["code"] == "INVALID_CREDENTIALS"


def test_login_success_returns_last_subject(client):
    client.post("/api/auth/register",
                json={"email": "c@example.com", "password": "password123"})
    resp = client.post("/api/auth/login",
                       json={"email": "c@example.com", "password": "password123"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["token"]
    assert data["lastSubjectId"] is None or isinstance(data["lastSubjectId"], str)


def test_me_and_unauthenticated(client, auth):
    """AC-1.3 无 token 访问受保护 API → 401。"""
    resp = client.get("/api/auth/me", headers=auth["headers"])
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["subjects"][0]["name"] == "本人"

    anon = client.get("/api/auth/me")
    assert anon.status_code == 401
    assert anon.json()["code"] == "UNAUTHENTICATED"


def test_expired_token_401(client, auth):
    """AC-1.3 过期 JWT → 401 TOKEN_EXPIRED。"""
    from app.auth.security import create_access_token

    expired = create_access_token(auth["account"]["id"], expires_minutes=-1)
    resp = client.get("/api/auth/me",
                      headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "TOKEN_EXPIRED"


def test_refresh_and_garbage_token(client, auth):
    resp = client.post("/api/auth/refresh", headers=auth["headers"])
    assert resp.status_code == 200
    assert resp.json()["data"]["token"]

    bad = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert bad.status_code == 401
