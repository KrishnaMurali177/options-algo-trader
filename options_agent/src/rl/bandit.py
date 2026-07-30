"""Linear contextual bandits: LinUCB and Thompson sampling. numpy only.

Why a linear bandit and not deep RL
-----------------------------------
The unit of experience is the symbol-day, and there are only ~250 sessions per
symbol per year — roughly 2-5k rows pooled across the universe. That supports a
per-arm ridge regression with ~22 features. It does not support a neural policy,
which would memorise the year and report a fake edge.

Full feedback
-------------
``replay_day`` is a deterministic simulator, so we can score *every* arm on
*every* day rather than only the arm that was played. That means:

  * training can use all arms' rewards per row (:meth:`fit_full_feedback`),
    which is far more sample-efficient than one-arm-per-row updates;
  * no importance weighting is needed anywhere;
  * a per-day oracle and exact regret are both computable.

The exploration machinery (UCB width, posterior sampling) is therefore not
strictly needed offline. It is kept because it is what a *live* deployment would
run, and because the UCB width is a useful honesty check: if the model only wins
with the bonus set to zero, it is not actually confident.
"""

from __future__ import annotations

import numpy as np


class LinUCB:
    """Disjoint LinUCB — an independent ridge model per arm.

    Disjoint (per-arm weights) rather than hybrid/shared because the arms are
    not smooth variations of one underlying knob: 'sit out' and 'permissive'
    have genuinely unrelated reward surfaces.
    """

    def __init__(self, n_arms: int, n_features: int, alpha: float = 1.0,
                 ridge: float = 1.0) -> None:
        if n_arms < 2 or n_features < 1:
            raise ValueError("need >=2 arms and >=1 feature")
        self.n_arms = n_arms
        self.n_features = n_features
        self.alpha = float(alpha)          # exploration width; 0 => pure greedy
        self.ridge = float(ridge)
        self.A = np.stack([np.eye(n_features) * self.ridge for _ in range(n_arms)])
        self.b = np.zeros((n_arms, n_features))

    # ── fitting ──────────────────────────────────────────────────────────
    def update(self, arm: int, x: np.ndarray, reward: float) -> None:
        """Single (arm, context, reward) observation."""
        x = np.asarray(x, dtype=float).ravel()
        self.A[arm] += np.outer(x, x)
        self.b[arm] += reward * x

    def fit_full_feedback(self, X: np.ndarray, R: np.ndarray) -> "LinUCB":
        """Fit from a full-feedback reward matrix.

        ``X`` is (n_rows, n_features); ``R`` is (n_rows, n_arms) holding the
        reward each arm *would* have earned on that row. Vectorised per arm
        rather than looping rows.
        """
        X = np.atleast_2d(np.asarray(X, dtype=float))
        R = np.atleast_2d(np.asarray(R, dtype=float))
        if X.shape[0] != R.shape[0]:
            raise ValueError(f"X has {X.shape[0]} rows, R has {R.shape[0]}")
        if R.shape[1] != self.n_arms:
            raise ValueError(f"R has {R.shape[1]} arms, expected {self.n_arms}")
        gram = X.T @ X
        for a in range(self.n_arms):
            self.A[a] += gram
            self.b[a] += X.T @ R[:, a]
        return self

    # ── prediction ───────────────────────────────────────────────────────
    def theta(self) -> np.ndarray:
        """(n_arms, n_features) ridge coefficients."""
        return np.stack([np.linalg.solve(self.A[a], self.b[a])
                         for a in range(self.n_arms)])

    def scores(self, x: np.ndarray) -> np.ndarray:
        """Per-arm UCB score for one context."""
        x = np.asarray(x, dtype=float).ravel()
        out = np.empty(self.n_arms)
        for a in range(self.n_arms):
            A_inv_x = np.linalg.solve(self.A[a], x)
            mean = float(self.b[a] @ A_inv_x)
            width = self.alpha * float(np.sqrt(max(x @ A_inv_x, 0.0)))
            out[a] = mean + width
        return out

    def expected_rewards(self, x: np.ndarray) -> np.ndarray:
        """Per-arm point estimate with no exploration bonus."""
        x = np.asarray(x, dtype=float).ravel()
        return self.theta() @ x

    def select(self, x: np.ndarray) -> int:
        return int(np.argmax(self.scores(x)))


class LinTS:
    """Linear Thompson sampling with a Normal-Inverse-Gamma conjugate prior.

    Unlike LinUCB this models reward *noise* explicitly, which matters here: day
    rewards are heavy-tailed even after clipping, and a fixed UCB width tends to
    over-explore arms whose variance is genuinely high (``permissive``) while
    under-exploring the quiet ones.
    """

    def __init__(self, n_arms: int, n_features: int, ridge: float = 1.0,
                 a0: float = 1.0, b0: float = 1.0, seed: int | None = 0) -> None:
        self.n_arms = n_arms
        self.n_features = n_features
        self.ridge = float(ridge)
        self.a0 = float(a0)
        self.b0 = float(b0)
        self.rng = np.random.default_rng(seed)
        self.A = np.stack([np.eye(n_features) * self.ridge for _ in range(n_arms)])
        self.b = np.zeros((n_arms, n_features))
        self.n = np.zeros(n_arms)
        self.yy = np.zeros(n_arms)   # sum of squared rewards, for the noise posterior

    def update(self, arm: int, x: np.ndarray, reward: float) -> None:
        x = np.asarray(x, dtype=float).ravel()
        self.A[arm] += np.outer(x, x)
        self.b[arm] += reward * x
        self.n[arm] += 1
        self.yy[arm] += reward * reward

    def fit_full_feedback(self, X: np.ndarray, R: np.ndarray) -> "LinTS":
        X = np.atleast_2d(np.asarray(X, dtype=float))
        R = np.atleast_2d(np.asarray(R, dtype=float))
        gram = X.T @ X
        for a in range(self.n_arms):
            self.A[a] += gram
            self.b[a] += X.T @ R[:, a]
            self.n[a] += X.shape[0]
            self.yy[a] += float(R[:, a] @ R[:, a])
        return self

    def _posterior(self, arm: int) -> tuple[np.ndarray, np.ndarray, float, float]:
        A_inv = np.linalg.inv(self.A[arm])
        mu = A_inv @ self.b[arm]
        a_n = self.a0 + self.n[arm] / 2.0
        # b_n from the standard NIG update; floored so a perfect in-sample fit
        # doesn't collapse the noise scale to zero.
        b_n = self.b0 + 0.5 * max(self.yy[arm] - float(self.b[arm] @ mu), 0.0)
        return mu, A_inv, float(a_n), float(max(b_n, 1e-9))

    def sample_rewards(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float).ravel()
        out = np.empty(self.n_arms)
        for a in range(self.n_arms):
            mu, A_inv, a_n, b_n = self._posterior(a)
            sigma2 = b_n / self.rng.gamma(shape=a_n, scale=1.0)   # inverse-gamma draw
            cov = sigma2 * A_inv
            cov = 0.5 * (cov + cov.T)                              # symmetrise
            theta = self.rng.multivariate_normal(mu, cov, method="eigh")
            out[a] = float(theta @ x)
        return out

    def expected_rewards(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float).ravel()
        return np.array([self._posterior(a)[0] @ x for a in range(self.n_arms)])

    def select(self, x: np.ndarray) -> int:
        return int(np.argmax(self.sample_rewards(x)))


def greedy_select(model, X: np.ndarray) -> np.ndarray:
    """Arm choice per row using point estimates only (no exploration).

    This is what an offline-evaluated policy should be scored on: exploration is
    a cost you pay to learn online, and charging it to a walk-forward backtest
    understates a policy that has already seen the training window.
    """
    X = np.atleast_2d(np.asarray(X, dtype=float))
    return np.array([int(np.argmax(model.expected_rewards(x))) for x in X])
