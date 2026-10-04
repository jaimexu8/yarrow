from tests.conftest import DEFAULT_PASSWORD, unique_email


# Verify that given correct current password, the account update endpoint successfully updates the user's information
async def test_correct_current_password_updates_the_account(
    client, auth_headers, login
):
    old_email = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()[
        "email"
    ]
    new_email = unique_email("updated")

    response = await client.put(
        "/api/v1/auth/me",
        headers=auth_headers,
        json={
            "name": "Renamed User",
            "email": new_email,
            "current_password": DEFAULT_PASSWORD,
            "password": "a-brand-new-password",
        },
    )

    assert response.status_code == 204, response.text
    me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
    assert me["name"] == "Renamed User"
    assert me["email"] == new_email
    assert (await login(new_email, "a-brand-new-password")).status_code == 200
    assert (await login(new_email, DEFAULT_PASSWORD)).status_code == 401
    assert (await login(old_email, "a-brand-new-password")).status_code == 401


# Verify that given incorrect current password, the account update endpoint returns an error
async def test_incorrect_current_password_is_rejected(client, auth_headers, login):
    before = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()

    response = await client.put(
        "/api/v1/auth/me",
        headers=auth_headers,
        json={
            "name": "Renamed User",
            "email": unique_email("updated"),
            "current_password": "not-my-password",
            "password": "a-brand-new-password",
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "INVALID_PASSWORD"

    # Nothing of the account should have changed
    assert (await client.get("/api/v1/auth/me", headers=auth_headers)).json() == before
    assert (await login(before["email"], DEFAULT_PASSWORD)).status_code == 200


# Verify that omitting the current password results in an error
async def test_missing_current_password_is_rejected(client, auth_headers, login):
    before = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()

    response = await client.put(
        "/api/v1/auth/me",
        headers=auth_headers,
        json={
            "name": "Renamed User",
            "email": unique_email("updated"),
            "password": "a-brand-new-password",
        },
    )

    assert response.status_code == 422, response.text
    assert ["body", "current_password"] in [
        issue["loc"] for issue in response.json()["detail"]
    ]

    # Nothing of the account should have changed
    assert (await client.get("/api/v1/auth/me", headers=auth_headers)).json() == before
    assert (await login(before["email"], DEFAULT_PASSWORD)).status_code == 200
