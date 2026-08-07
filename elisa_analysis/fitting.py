"""4-Parameter and 5-Parameter logistic (4PL/5PL) curve fitting for ELISA standard curves.

Model conventions (matching GraphPad Prism / MyAssays Pro):

    4PL:  y = D + (A - D) / (1 + (x / C) ** B)
    5PL:  y = D + (A - D) / (1 + (x / C) ** B) ** E

    A = response at x -> 0      (bottom/top asymptote depending on curve direction)
    D = response at x -> inf    (the other asymptote)
    C = EC50 / IC50 (inflection point, in concentration units)
    B = Hill slope
    E = asymmetry factor (5PL only; E = 1 reduces 5PL to 4PL)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

import numpy as np
from scipy.optimize import curve_fit

WeightMode = Literal["none", "1/y", "1/y2"]


def logistic_4pl(x: np.ndarray, a: float, b: float, c: float, d: float) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return d + (a - d) / (1.0 + np.power(np.where(x > 0, x, np.nan) / c, b))


def logistic_5pl(x: np.ndarray, a: float, b: float, c: float, d: float, e: float) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return d + (a - d) / np.power(1.0 + np.power(np.where(x > 0, x, np.nan) / c, b), e)


def inverse_4pl(y: np.ndarray, a: float, b: float, c: float, d: float) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    ratio = (a - d) / (y - d) - 1.0
    with np.errstate(invalid="ignore"):
        x = c * np.power(np.where(ratio > 0, ratio, np.nan), 1.0 / b)
    return x


def inverse_5pl(y: np.ndarray, a: float, b: float, c: float, d: float, e: float) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    base = (a - d) / (y - d)
    with np.errstate(invalid="ignore"):
        inner = np.power(np.where(base > 0, base, np.nan), 1.0 / e) - 1.0
        x = c * np.power(np.where(inner > 0, inner, np.nan), 1.0 / b)
    return x


@dataclass
class FitResult:
    model: Literal["4PL", "5PL"]
    params: dict
    covariance: Optional[np.ndarray]
    r_squared: float
    x: np.ndarray
    y: np.ndarray
    weights: Optional[np.ndarray]
    predict: Callable[[np.ndarray], np.ndarray] = field(repr=False)
    invert: Callable[[np.ndarray], np.ndarray] = field(repr=False)
    warnings: list = field(default_factory=list)

    def concentration_at(self, response: np.ndarray) -> np.ndarray:
        return self.invert(response)

    def response_at(self, conc: np.ndarray) -> np.ndarray:
        return self.predict(conc)

    def valid_response_range(self):
        lo, hi = self.params["A"], self.params["D"]
        return (min(lo, hi), max(lo, hi))


def _initial_guess_4pl(x: np.ndarray, y: np.ndarray):
    a0 = y[np.argmin(x)]
    d0 = y[np.argmax(x)]
    c0 = np.median(x[x > 0]) if np.any(x > 0) else 1.0
    b0 = 1.0 if d0 >= a0 else -1.0
    return [a0, b0, c0, d0]


def _initial_guess_5pl(x: np.ndarray, y: np.ndarray):
    a0, b0, c0, d0 = _initial_guess_4pl(x, y)
    return [a0, b0, c0, d0, 1.0]


def fit_curve(
    x: np.ndarray,
    y: np.ndarray,
    model: Literal["4PL", "5PL"] = "4PL",
    weight_mode: WeightMode = "1/y2",
) -> FitResult:
    """Fit a 4PL or 5PL standard curve to (concentration, response) pairs.

    Points with non-positive concentration (e.g. a 0-standard / blank) are
    excluded from the log-domain fit but the resulting curve still passes
    through them approximately via the A asymptote.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y) & (x > 0)
    xf, yf = x[mask], y[mask]
    if xf.size < (4 if model == "4PL" else 5):
        raise ValueError(
            f"Need at least {4 if model == '4PL' else 5} standard points with "
            f"positive concentration to fit a {model} curve; got {xf.size}."
        )

    if weight_mode == "none":
        sigma = None
    elif weight_mode == "1/y":
        sigma = np.sqrt(np.abs(yf)) + 1e-9
    elif weight_mode == "1/y2":
        sigma = np.abs(yf) + 1e-9
    else:
        raise ValueError(f"Unknown weight_mode: {weight_mode}")

    # Generous but finite bounds on the asymptotes (A/D) and EC50 (C) keep the
    # optimizer from diverging to non-physical values when the data don't
    # reach one of the plateaus (e.g. a saturated/excluded top standard) --
    # without them, a 4PL/5PL can trade off C against D and still fit the
    # rising limb almost perfectly while landing on meaningless parameters.
    y_span = max(float(np.ptp(yf)), 1e-6)
    y_lo, y_hi = float(np.min(yf)), float(np.max(yf))
    x_lo, x_hi = float(np.min(xf)), float(np.max(xf))
    asym_bounds = (y_lo - 5 * y_span, y_hi + 5 * y_span)
    c_bounds = (x_lo / 50.0, x_hi * 50.0)
    b_bounds = (-50.0, 50.0)

    if model == "4PL":
        func = logistic_4pl
        p0 = _initial_guess_4pl(xf, yf)
        param_names = ["A", "B", "C", "D"]
        lower = [asym_bounds[0], b_bounds[0], c_bounds[0], asym_bounds[0]]
        upper = [asym_bounds[1], b_bounds[1], c_bounds[1], asym_bounds[1]]
    elif model == "5PL":
        func = logistic_5pl
        p0 = _initial_guess_5pl(xf, yf)
        param_names = ["A", "B", "C", "D", "E"]
        lower = [asym_bounds[0], b_bounds[0], c_bounds[0], asym_bounds[0], 0.05]
        upper = [asym_bounds[1], b_bounds[1], c_bounds[1], asym_bounds[1], 20.0]
    else:
        raise ValueError("model must be '4PL' or '5PL'")

    p0_clipped = np.clip(p0, lower, upper)
    popt, pcov = curve_fit(
        func, xf, yf, p0=p0_clipped, sigma=sigma, absolute_sigma=False,
        bounds=(lower, upper), maxfev=20000,
    )
    params = dict(zip(param_names, popt))

    y_pred = func(xf, *popt)
    ss_res = np.nansum((yf - y_pred) ** 2)
    ss_tot = np.nansum((yf - np.mean(yf)) ** 2)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    warnings = []
    if not (x_lo <= params["C"] <= x_hi):
        warnings.append(
            f"EC50 (C={params['C']:.4g}) falls outside the tested standard range "
            f"[{x_lo:.4g}, {x_hi:.4g}] -- one plateau isn't constrained by data "
            "(e.g. a top/bottom standard was excluded or missing). Treat the fit "
            "as extrapolated and consider adding a standard beyond this range."
        )
    tol = 1e-6
    if any(abs(p - lo) <= tol * max(1, abs(lo)) or abs(p - hi) <= tol * max(1, abs(hi)) for p, lo, hi in zip(popt, lower, upper)):
        warnings.append(
            "One or more fitted parameters landed at the edge of their allowed range, "
            "meaning the data don't clearly determine it -- results may be unreliable."
        )

    if model == "4PL":
        predict = lambda xx: logistic_4pl(xx, *popt)
        invert = lambda yy: inverse_4pl(yy, *popt)
    else:
        predict = lambda xx: logistic_5pl(xx, *popt)
        invert = lambda yy: inverse_5pl(yy, *popt)

    return FitResult(
        model=model,
        params=params,
        covariance=pcov,
        r_squared=r_squared,
        x=xf,
        y=yf,
        weights=sigma,
        predict=predict,
        invert=invert,
        warnings=warnings,
    )
