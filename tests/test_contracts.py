from dataclasses import fields

from core.contracts import PROFILES, T0, Plan, WardState, fake_plan, fake_ward_state, to_dict


def test_fakes_construct():
    assert isinstance(fake_ward_state(), WardState)
    assert isinstance(fake_plan(), Plan)


def test_to_dict_has_dataclass_field_names():
    ws = fake_ward_state()
    plan = fake_plan()
    assert set(to_dict(ws)) == {f.name for f in fields(WardState)}
    assert set(to_dict(plan)) == {f.name for f in fields(Plan)}


def test_profiles_and_t0():
    assert len(PROFILES) == 3
    assert set(PROFILES) == {"safety_first", "balanced", "max_capacity"}
    assert T0 == "2026-11-21T09:00"


def test_escalated_passthrough():
    assert fake_ward_state(escalated=["P-002"]).escalated == ["P-002"]
