"""Phase 2 tests: Settings — people, restrictions (incl. never-level safety rules), staples."""

from sqlmodel import Session, select

from app import db
from app.models import Person, Restriction, Staple

HTMX = {"HX-Request": "true"}


def get_restriction(term):
    with Session(db.get_engine()) as s:
        return s.exec(select(Restriction).where(Restriction.term == term)).one_or_none()


def get_person(person_id):
    with Session(db.get_engine()) as s:
        return s.get(Person, person_id)


def staple_names():
    with Session(db.get_engine()) as s:
        return {st.name: st.enabled for st in s.exec(select(Staple))}


def add_restriction(client, **fields):
    data = {"person_id": 1, "term": "mushrooms", "level": "avoid",
            "aliases": "", "exceptions": "", "notes": ""}
    data.update(fields)
    return client.post("/settings/restrictions", data=data, headers=HTMX)


def save_restriction(client, r, **fields):
    """Post the edit form for `r`, starting from its current values."""
    data = {"term": r.term, "level": r.level, "aliases": ", ".join(r.aliases),
            "exceptions": ", ".join(r.exceptions), "notes": r.notes or ""}
    data.update(fields)
    return client.post(f"/settings/restrictions/{r.id}", data=data, headers=HTMX)


# ---------- Page ----------


def test_settings_page_shows_seeded_data(client):
    html = client.get("/settings").text
    for text in ["Alex", "Jordan", "shrimp", "XO sauce", "broccoli", "cornstarch", "olive oil"]:
        assert text in html


def test_never_restrictions_have_no_toggle(client):
    html = client.get("/settings").text
    corn = get_restriction("corn")
    fish = get_restriction("fish")
    assert f"/settings/restrictions/{corn.id}/toggle" not in html
    assert f"/settings/restrictions/{fish.id}/toggle" in html


# ---------- People ----------


def test_rename_person(client):
    response = client.post("/settings/people/2", data={"name": "  Sam  "}, headers=HTMX)
    assert response.status_code == 200
    assert "Saved." in response.text
    assert get_person(2).name == "Sam"


def test_blank_name_rejected(client):
    response = client.post("/settings/people/2", data={"name": "   "}, headers=HTMX)
    assert "can&#39;t be empty" in response.text  # apostrophe is HTML-escaped
    assert get_person(2).name == "Jordan"


def test_too_long_name_rejected(client):
    client.post("/settings/people/2", data={"name": "x" * 41}, headers=HTMX)
    assert get_person(2).name == "Jordan"


def test_rename_unknown_person_404(client):
    assert client.post("/settings/people/99", data={"name": "X"}).status_code == 404


# ---------- Adding restrictions ----------


def test_add_restriction(client):
    response = add_restriction(
        client, person_id=2, term=" Mushrooms ", level="never",
        aliases="Shiitake, portobello\nshiitake", exceptions="", notes=" gut ",
    )
    assert response.status_code == 200
    r = get_restriction("Mushrooms")
    assert r.person_id == 2
    assert r.level == "never"
    assert r.aliases == ["Shiitake", "portobello"]  # case kept, repeat dropped
    assert r.exceptions == []
    assert r.notes == "gut"
    assert r.active


def test_add_restriction_requires_term(client):
    response = add_restriction(client, term="  ")
    assert "Enter an ingredient" in response.text


def test_add_restriction_rejects_bad_level(client):
    response = add_restriction(client, level="sometimes")
    assert "Pick a level" in response.text
    assert get_restriction("mushrooms") is None


def test_add_duplicate_term_for_same_person_rejected(client):
    response = add_restriction(client, person_id=2, term="Corn", level="avoid")
    assert "already a restriction" in response.text
    with Session(db.get_engine()) as s:
        corns = s.exec(select(Restriction).where(Restriction.term.ilike("corn"))).all()
    assert len(corns) == 1


def test_same_term_for_other_person_allowed(client):
    add_restriction(client, person_id=1, term="corn", level="avoid")
    with Session(db.get_engine()) as s:
        corns = s.exec(select(Restriction).where(Restriction.term == "corn")).all()
    assert {c.person_id for c in corns} == {1, 2}


def test_add_error_keeps_what_was_typed(client):
    response = add_restriction(client, person_id=2, term="corn", aliases="maize")
    assert 'value="maize"' in response.text


# ---------- Editing restrictions ----------


def test_edit_form_and_cancel(client):
    corn = get_restriction("corn")
    form = client.get(f"/settings/restrictions/{corn.id}/edit")
    assert 'name="term"' in form.text
    assert "cornstarch, corn starch" in form.text
    card = client.get(f"/settings/restrictions/{corn.id}")
    assert 'name="term"' not in card.text


def test_edit_restriction_aliases_and_exceptions(client):
    corn = get_restriction("corn")
    save_restriction(client, corn, aliases="maize, sweetcorn", exceptions="cornstarch")
    corn = get_restriction("corn")
    assert corn.aliases == ["maize", "sweetcorn"]
    assert corn.exceptions == ["cornstarch"]
    assert corn.level == "never"


def test_edit_to_duplicate_term_rejected(client):
    broccoli = get_restriction("broccoli")
    response = save_restriction(client, broccoli, term="CORN")
    assert "already a restriction" in response.text
    assert get_restriction("broccoli") is not None


def test_edit_rejects_bad_level(client):
    corn = get_restriction("corn")
    save_restriction(client, corn, level="sometimes")
    assert get_restriction("corn").level == "never"


# ---------- Safety rules for `never` ----------


def test_downgrade_never_needs_confirmation(client):
    corn = get_restriction("corn")
    response = save_restriction(client, corn, level="avoid")
    assert "Tap Save again to confirm" in response.text
    assert 'name="confirm_downgrade"' in response.text
    assert get_restriction("corn").level == "never"  # unchanged so far

    save_restriction(client, corn, level="avoid", confirm_downgrade="true")
    assert get_restriction("corn").level == "avoid"


def test_never_restriction_cannot_be_toggled_off(client):
    corn = get_restriction("corn")
    response = client.post(f"/settings/restrictions/{corn.id}/toggle", headers=HTMX)
    assert response.status_code == 400
    assert get_restriction("corn").active


def test_avoid_restriction_toggles(client):
    fish = get_restriction("fish")
    client.post(f"/settings/restrictions/{fish.id}/toggle", headers=HTMX)
    assert not get_restriction("fish").active
    client.post(f"/settings/restrictions/{fish.id}/toggle", headers=HTMX)
    assert get_restriction("fish").active


def test_upgrading_inactive_avoid_to_never_turns_it_on(client):
    fish = get_restriction("fish")
    client.post(f"/settings/restrictions/{fish.id}/toggle", headers=HTMX)
    assert not get_restriction("fish").active

    save_restriction(client, get_restriction("fish"), level="never")
    fish = get_restriction("fish")
    assert fish.level == "never"
    assert fish.active


def test_delete_restriction(client):
    fish = get_restriction("fish")
    response = client.post(f"/settings/restrictions/{fish.id}/delete", headers=HTMX)
    assert response.status_code == 200
    assert response.text == ""
    assert get_restriction("fish") is None


def test_delete_button_asks_for_confirmation(client):
    html = client.get("/settings").text
    assert html.count("hx-confirm=") == 4  # one per seeded restriction


# ---------- Staples ----------


def test_add_staples_dedupes(client):
    response = client.post(
        "/settings/staples", data={"names": "Soy Sauce, rice\nEgg, soy sauce"}, headers=HTMX
    )
    names = staple_names()
    assert names["soy sauce"] is True
    assert names["rice"] is True
    assert "egg" not in names  # "eggs" is already a staple
    assert "egg is already a staple" in response.text


def test_toggle_staple(client):
    with Session(db.get_engine()) as s:
        salt = s.exec(select(Staple).where(Staple.name == "salt")).one()
    client.post(f"/settings/staples/{salt.id}/toggle", headers=HTMX)
    assert staple_names()["salt"] is False
    client.post(f"/settings/staples/{salt.id}/toggle", headers=HTMX)
    assert staple_names()["salt"] is True


def test_delete_staple(client):
    with Session(db.get_engine()) as s:
        sugar = s.exec(select(Staple).where(Staple.name == "sugar")).one()
    response = client.post(f"/settings/staples/{sugar.id}/delete", headers=HTMX)
    assert 'id="staples"' in response.text
    assert "sugar" not in staple_names()


def test_disabled_staple_hidden_from_pantry_summary(client):
    with Session(db.get_engine()) as s:
        sugar = s.exec(select(Staple).where(Staple.name == "sugar")).one()
    client.post(f"/settings/staples/{sugar.id}/toggle", headers=HTMX)
    assert "sugar" not in client.get("/pantry").text
