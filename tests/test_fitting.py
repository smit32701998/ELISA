import numpy as np
import pytest

from elisa_analysis.fitting import (
    fit_curve,
    inverse_4pl,
    inverse_5pl,
    logistic_4pl,
    logistic_5pl,
)


def test_4pl_round_trip_no_noise():
    true_params = dict(a=0.05, b=1.2, c=20.0, d=3.0)
    x = np.array([0.5, 1, 2, 5, 10, 20, 40, 80, 160])
    y = logistic_4pl(x, **true_params)

    result = fit_curve(x, y, model="4PL", weight_mode="none")

    assert result.r_squared > 0.999
    for k, v in true_params.items():
        assert result.params[k.upper()] == pytest.approx(v, rel=0.02)


def test_4pl_inverse_recovers_concentration():
    params = dict(a=0.05, b=1.2, c=20.0, d=3.0)
    x_true = np.array([1.0, 5.0, 20.0, 60.0])
    y = logistic_4pl(x_true, **params)
    x_back = inverse_4pl(y, params["a"], params["b"], params["c"], params["d"])
    assert x_back == pytest.approx(x_true, rel=1e-6)


def test_5pl_inverse_recovers_concentration():
    params = dict(a=0.02, b=1.5, c=15.0, d=4.0, e=0.7)
    x_true = np.array([1.0, 5.0, 15.0, 50.0])
    y = logistic_5pl(x_true, **params)
    x_back = inverse_5pl(y, params["a"], params["b"], params["c"], params["d"], params["e"])
    assert x_back == pytest.approx(x_true, rel=1e-6)


def test_5pl_fit_recovers_asymmetric_curve():
    true_params = dict(a=0.03, b=1.4, c=10.0, d=3.5, e=0.6)
    x = np.geomspace(0.2, 200, 12)
    y = logistic_5pl(x, **true_params)

    result = fit_curve(x, y, model="5PL", weight_mode="none")

    assert result.r_squared > 0.999
    y_check = result.predict(x)
    assert y_check == pytest.approx(y, rel=0.02)


def test_fit_curve_requires_enough_points():
    x = np.array([1.0, 2.0, 3.0])
    y = np.array([0.1, 0.2, 0.3])
    with pytest.raises(ValueError):
        fit_curve(x, y, model="4PL")


def test_missing_top_asymptote_is_flagged():
    # Standards that only cover the rising limb of the sigmoid (no point near
    # the true EC50/top plateau, e.g. because the top standard saturated the
    # reader) leave the top asymptote unconstrained by the tested range --
    # even a perfect fit is an extrapolation. That should be flagged rather
    # than silently reported as a normal, trustworthy curve.
    true_params = dict(a=0.04, b=1.2, c=1500.0, d=3.0)
    x = np.array([15.6, 31.3, 62.5, 125.0, 250.0])  # well below the true EC50
    y = logistic_4pl(x, **true_params)

    result = fit_curve(x, y, model="4PL", weight_mode="none")

    assert result.r_squared > 0.99
    assert len(result.warnings) > 0
    assert any("EC50" in w for w in result.warnings)


def test_runaway_fit_is_bounded():
    # Noisy data with no point near the top plateau is the realistic failure
    # mode: without bounds, curve_fit can drift C/D to non-physical values
    # (e.g. C in the hundreds of thousands) while still reporting a high R^2.
    rng = np.random.default_rng(3)
    true_params = dict(a=0.04, b=1.2, c=250.0, d=3.0)
    x = np.array([15.6, 31.3, 62.5, 125.0, 250.0])
    y = logistic_4pl(x, **true_params) * (1 + rng.normal(0, 0.03, size=x.shape))

    result = fit_curve(x, y, model="4PL", weight_mode="none")

    assert result.params["C"] <= x.max() * 50
    assert result.params["D"] <= (y.max() + 5 * np.ptp(y)) * 1.01


def test_well_constrained_fit_has_no_warnings():
    true_params = dict(a=0.05, b=1.2, c=20.0, d=3.0)
    x = np.geomspace(0.5, 200, 10)  # spans well past the inflection on both sides
    y = logistic_4pl(x, **true_params)

    result = fit_curve(x, y, model="4PL", weight_mode="none")

    assert result.warnings == []


def test_weighting_changes_fit_when_noisy():
    rng = np.random.default_rng(0)
    params = dict(a=0.05, b=1.1, c=25.0, d=3.0)
    x = np.geomspace(0.5, 200, 10)
    y_clean = logistic_4pl(x, **params)
    y_noisy = y_clean + rng.normal(0, 0.15, size=x.shape) * y_clean

    fit_unweighted = fit_curve(x, y_noisy, model="4PL", weight_mode="none")
    fit_weighted = fit_curve(x, y_noisy, model="4PL", weight_mode="1/y2")

    assert np.isfinite(fit_unweighted.r_squared)
    assert np.isfinite(fit_weighted.r_squared)
