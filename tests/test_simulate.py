from leadflow.simulate import simulate


def test_simulation_is_deterministic_and_consistent():
    a, b = simulate(), simulate()
    assert a == b
    assert a["leads"] > 30 and a["spam_blocked"] >= 1
    f = a["funnel"]
    assert f["new"] >= f["contacted"] >= f["booked"] >= f["attended"]
    assert a["median_urgent_response_min"] < a["median_response_min"]  # urgențele sunt sunate primele
    assert a["crm_deals"] == a["leads"]  # fiecare lead real (nu duplicat, nu spam) ajunge în CRM
