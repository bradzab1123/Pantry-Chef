"""First-run setup: naming the two people on a new install."""

import pytest
from sqlmodel import Session, select

from app import db
from app.main import TABS
from app.models import Person

HTMX = {"HX-Request": "true"}


def names():
    with Session(db.get_engine()) as s:
        return [p.name for p in s.exec(select(Person).order_by(Person.id))]


@pytest.mark.parametrize("url", [url for _key, _label, url in TABS])
def test_new_install_sends_every_page_to_setup(new_install_client, url):
    response = new_install_client.get(url, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


def test_setup_page_has_no_tab_bar(new_install_client):
    html = new_install_client.get("/setup").text
    assert "Who's cooking?" in html
    assert 'name="name-1"' in html and 'name="name-2"' in html
    assert 'class="tabbar"' not in html


def test_infrastructure_works_before_setup(new_install_client):
    assert new_install_client.get("/health").status_code == 200
    assert new_install_client.get("/sw.js").status_code == 200
    assert new_install_client.get("/static/style.css").status_code == 200


def test_setup_saves_names_then_goes_to_settings(new_install_client):
    response = new_install_client.post(
        "/setup", data={"name-1": "  Riley ", "name-2": "Sam"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/settings"
    assert names() == ["Riley", "Sam"]
    # Set up now: pages load, and /setup itself bounces home.
    assert new_install_client.get("/", follow_redirects=False).status_code == 200
    assert new_install_client.get("/setup", follow_redirects=False).headers["location"] == "/"


@pytest.mark.parametrize("form,error", [
    ({"name-1": "Riley", "name-2": "   "}, "can&#39;t be empty"),
    ({"name-1": "x" * 41, "name-2": "Sam"}, "Keep it under 40"),
    ({"name-1": "Sam", "name-2": "sam"}, "need to be different"),
])
def test_setup_rejects_bad_names(new_install_client, form, error):
    response = new_install_client.post("/setup", data=form)
    assert response.status_code == 200
    assert error in response.text
    assert names() == ["", ""]  # nothing saved, not even the good name


def test_setup_error_keeps_what_was_typed(new_install_client):
    html = new_install_client.post("/setup", data={"name-1": "Riley", "name-2": ""}).text
    assert 'value="Riley"' in html


def test_setup_is_closed_once_done(client):
    response = client.post("/setup", data={"name-1": "X", "name-2": "Y"}, follow_redirects=False)
    assert response.headers["location"] == "/"
    assert names() == ["Alex", "Jordan"]


def test_rename_to_the_other_persons_name_rejected(client):
    response = client.post("/settings/people/2", data={"name": "alex"}, headers=HTMX)
    assert "need to be different" in response.text
    assert names() == ["Alex", "Jordan"]
