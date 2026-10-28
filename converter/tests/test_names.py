from pathlib import Path

from gtfsconv import names


def test_stray_n_prefix_removed():
    cases = {
        "N Av. Dr. Lourenço Peixinho - CTT B": "Av. Dr. Lourenço Peixinho - CTT B",
        "N Eixo - Praca J. Magalhães Lima B": "Eixo - Praca J. Magalhães Lima B",
        "N Escolas de Nariz": "Escolas de Nariz",
        "N Fonte da Vessada": "Fonte da Vessada",
        "N Lavadouros de Sarrazola B": "Lavadouros de Sarrazola B",
    }
    for raw, want in cases.items():
        assert names.clean_raw(raw) == want


def test_run_together_abbreviations():
    assert names.clean_raw("Av.25deAbril -EscolaJoseEstevaoB") == "Av. 25 de Abril - Escola Jose Estevao B"


def test_real_hyphenated_names_are_kept():
    assert names.clean_raw("Albergaria-a-Nova A") == "Albergaria-a-Nova A"
    assert names.clean_raw("Z.I. Padrões") == "Z.I. Padrões"


def test_side_suffix_is_kept_and_parsed():
    assert names.split_side("Eucalipto B") == ("Eucalipto", "B")
    assert names.split_side("R. Clube dos Galitos - A") == ("R. Clube dos Galitos", "A")
    assert names.split_side("Terminal") == ("Terminal", "")


def test_alias_applied_on_cleaned_name(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("# comment\nraw,canonical\nIgreja Qulntas A,Igreja Quintas A\n", encoding="utf-8")
    al = names.load_aliases(f)
    assert names.canonical("Igreja Qulntas A", al) == "Igreja Quintas A"


def test_fused_name_detected():
    known = {"Terminal Rodoviário de Aveiro", "Caçadores 10 - Sé", "Caçadores 10 - Sé Terminal Rodoviário de Aveiro"}
    assert ("Caçadores 10 - Sé Terminal Rodoviário de Aveiro", "Terminal Rodoviário de Aveiro") in names.find_fused(known)


def test_cluster_suggestions_are_not_merged_across_sides():
    from collections import Counter
    c = {"10": Counter({"Igreja Quintas A": 5, "Igreja Qulntas A": 9, "Igreja Quintas B": 3})}
    groups = names.clusters(c)
    assert len(groups) == 1 and set(groups[0]["members"]) == {"Igreja Quintas A", "Igreja Qulntas A"}
