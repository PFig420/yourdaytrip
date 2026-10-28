import datetime as dt
from types import SimpleNamespace

import pytest

from gtfsconv import calendars as C
from gtfsconv.load import BuildError

D = dt.date


def cal(terms=True, municipal=None):
    t = [(D(2026, 9, 14), D(2026, 12, 18)), (D(2027, 1, 4), D(2027, 2, 12)), (D(2027, 2, 18), D(2027, 3, 19)),
         (D(2027, 4, 5), D(2027, 6, 18))] if terms else []
    return C.Calendar(D(2026, 10, 19), D(2027, 9, 12), municipal or {}, False, t)


def trip(day_type="Dias Úteis", season="", notes=()):
    return SimpleNamespace(day_type=day_type, season=season, notes=tuple(notes), trip="t", source="s", line="1")


def on(t, d, c=None, hol=None):
    c = c or cal()
    _, _, _, pred = C.trip_rule(t, c)
    return pred(d, c.holidays("busway") if hol is None else hol)


def test_national_holidays_in_window_exactly():
    c = cal()
    got = sorted(d for d in c.holidays("busway") if c.start <= d <= c.end)
    want = [D(2026, 11, 1), D(2026, 12, 1), D(2026, 12, 8), D(2026, 12, 25), D(2027, 1, 1), D(2027, 3, 26),
            D(2027, 3, 28), D(2027, 4, 25), D(2027, 5, 1), D(2027, 5, 27), D(2027, 6, 10), D(2027, 8, 15)]
    assert got == want


def test_municipal_holiday_only_for_its_agency():
    c = cal(municipal={"aveirobus": {(5, 12)}})
    assert D(2027, 5, 12) in c.holidays("aveirobus")
    assert D(2027, 5, 12) not in c.holidays("busway")


def test_easter_dates():
    assert C.easter(2027) == D(2027, 3, 28)
    assert C.easter(2026) == D(2026, 4, 5)


def test_domingos_ou_segunda_se_feriado():
    t = trip("Domingos ou 2.ª feira se feriado")
    hol = {D(2027, 3, 29)}  # synthetic Monday holiday
    assert not on(t, D(2027, 3, 28), hol=hol)   # the Sunday before is off
    assert on(t, D(2027, 3, 29), hol=hol)       # the Monday is on
    assert on(t, D(2027, 4, 4), hol=hol)        # an ordinary Sunday
    assert not on(t, D(2027, 4, 5), hol=hol)    # an ordinary Monday


@pytest.mark.parametrize("day_type,d,expected", [
    ("Dias Úteis", D(2026, 10, 21), True), ("Dias Úteis", D(2026, 10, 24), False), ("Dias Úteis", D(2026, 12, 8), False),
    ("Dias Úteis (exceto agosto)", D(2027, 8, 4), False), ("Dias Úteis (exceto agosto)", D(2027, 7, 14), True),
    ("3ª feiras (DU)", D(2026, 10, 20), True), ("3ª feiras (DU)", D(2026, 10, 21), False),
    ("Sábados", D(2026, 10, 24), True), ("Sábados", D(2026, 10, 25), False),
    ("Domingos", D(2026, 10, 25), True), ("Domingos", D(2026, 12, 8), False),
    ("Domingos e Feriados", D(2026, 12, 8), True), ("Domingos e Feriados", D(2026, 10, 24), False),
    ("Fim-de-semana e Feriados", D(2026, 10, 24), True), ("Fim-de-semana e Feriados", D(2026, 12, 25), True),
    ("Fim-de-semana e Feriados", D(2026, 10, 21), False),
    ("Sábados, Domingos e Feriados", D(2026, 10, 25), True),
    ("Todos os dias", D(2026, 10, 21), True),
    ("TODOS OS DIAS (Julho e Agosto)", D(2027, 7, 1), True), ("TODOS OS DIAS (Julho e Agosto)", D(2027, 6, 30), False),
])
def test_day_types(day_type, d, expected):
    assert on(trip(day_type), d) is expected


@pytest.mark.parametrize("season,d,expected", [
    ("A", D(2027, 8, 10), True), ("", D(2027, 8, 10), True),
    ("C", D(2026, 10, 21), True), ("C", D(2027, 7, 14), False), ("C", D(2027, 6, 30), True),
    ("D", D(2027, 7, 14), True), ("D", D(2026, 10, 21), False),
    ("E", D(2027, 6, 2), True), ("E", D(2027, 9, 1), True), ("E", D(2027, 5, 31), False), ("E", D(2026, 10, 21), False),
    ("EA", D(2027, 8, 4), False), ("EA", D(2027, 7, 14), True),
    ("PE", D(2026, 10, 21), True), ("PE", D(2026, 12, 23), False),
    ("FE", D(2026, 12, 23), True), ("FE", D(2026, 10, 21), False),
])
def test_season_codes(season, d, expected):
    assert on(trip("Todos os dias", season), d) is expected


def test_notes():
    school = trip(notes=["Escolar -periodo coincidente com o calendario escolar em vigor"])
    assert on(school, D(2026, 10, 21)) and not on(school, D(2026, 12, 23))
    qs = trip("Todos os dias", notes=["Escolar 4.a e 6.a feira - realiza-se a 4.a e 6.a feira no periodo coincidente com o calendario escolar em vigor"])
    assert on(qs, D(2026, 10, 21)) and on(qs, D(2026, 10, 23)) and not on(qs, D(2026, 10, 22)) and not on(qs, D(2026, 12, 23))
    tt = trip("Todos os dias", notes=["Escolar 2.a, 3.a e 5.a feira - realiza-se a 2.a, 3.a e 5.a feira no periodo coincidente com o calendario escolar em vigor"])
    assert on(tt, D(2026, 10, 19)) and on(tt, D(2026, 10, 20)) and on(tt, D(2026, 10, 22)) and not on(tt, D(2026, 10, 21))
    sep = trip("Domingos ou 2.ª feira se feriado", notes=["Realiza-se no período entre o 2.º domingo de setembro e até 30 de junho"])
    assert on(sep, D(2026, 10, 25)) and on(sep, D(2027, 6, 27)) and not on(sep, D(2027, 7, 4))
    assert on(sep, D(2027, 9, 12)) and C.nth_sunday(2027, 9, 2) == D(2027, 9, 12)
    ini = trip("Dias Úteis", notes=["j Realiza-se apenas do inicio da epoca escolar ate 30 de junho"])
    assert on(ini, D(2026, 10, 21)) and on(ini, D(2027, 6, 30)) and not on(ini, D(2027, 7, 1))


def test_informational_notes_have_no_effect():
    t = trip(notes=["Ligação à linha 16", "Estes hararios tem ligacao com as servicos efetuados pela linha 5"])
    assert on(t, D(2026, 10, 21))


def test_unrecognised_note_fails():
    with pytest.raises(BuildError, match="unrecognised note"):
        C.trip_rule(trip(notes=["Qualquer coisa nova"]), cal())


def test_empty_school_calendar_fails_with_clear_message():
    with pytest.raises(BuildError, match=r"\[school\] is empty"):
        C.trip_rule(trip(season="PE"), cal(terms=False))
    with pytest.raises(BuildError, match=r"\[school\] is empty"):
        C.trip_rule(trip(notes=["Escolar -periodo coincidente com o calendario escolar em vigor"]), cal(terms=False))
    C.trip_rule(trip(season="A"), cal(terms=False))  # no school need: fine


def test_unknown_day_type_and_season_fail():
    with pytest.raises(BuildError):
        C.trip_rule(trip("Feriados de Marte"), cal())
    with pytest.raises(BuildError):
        C.trip_rule(trip(season="ZZ"), cal())
