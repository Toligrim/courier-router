from courier_router.web import _hash_password, _verify_password, create_app


def test_password_hash_roundtrip():
    encoded = _hash_password("correct horse battery staple")
    assert encoded.startswith("scrypt$")
    assert _verify_password("correct horse battery staple", encoded)
    assert not _verify_password("wrong password", encoded)


def test_web_app_can_be_created_with_explicit_secret():
    app = create_app("test-secret")
    assert app.title == "Courier Router Web"
