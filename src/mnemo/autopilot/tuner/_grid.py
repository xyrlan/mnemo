"""Latin hypercube sampler for BM25F grid search.

No numpy dependency — uses stdlib random only. The sampler produces
stratified random samples across each dimension independently, then
combines them so each "stratum" (equally spaced bucket within each
dimension) is hit approximately once per N samples.

For dimensions with fewer than N distinct values, we tile the values
with shuffling so coverage remains even when n > len(values).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class SearchSpace:
    """Defines the grid dimensions and their candidate values."""
    dimensions: dict[str, list]


def BM25SearchSpace() -> SearchSpace:
    """The BM25F search space from the spec (600 total configs)."""
    return SearchSpace(
        dimensions={
            "b": [0.5, 0.65, 0.75, 0.85, 0.95],
            "k1": [0.8, 1.2, 1.5, 1.8, 2.2],
            "name_w": [1, 2, 3, 5],
            "topic_w": [1, 2, 3],
            "body_w": [1, 2],
        }
    )


def latin_hypercube(
    space: SearchSpace,
    n: int,
    *,
    rng: random.Random,
) -> list[dict]:
    """Return n stratified random samples from the search space.

    Each dimension is sampled independently with LHS stratification:
    - Divide the dimension into n equal buckets.
    - Pick one value per bucket (via the available discrete values).
    - Shuffle independently, then zip across dimensions.

    When n > len(values), we tile + shuffle to maintain even coverage.
    """
    if n == 0:
        return []

    # For each dimension, build a list of n values with LHS stratification
    per_dim: dict[str, list] = {}
    for dim_name, values in space.dimensions.items():
        k = len(values)
        if k == 0:
            per_dim[dim_name] = [None] * n
            continue
        # Build a tiled, shuffled list of length n
        tiled: list = []
        while len(tiled) < n:
            chunk = list(values)
            rng.shuffle(chunk)
            tiled.extend(chunk)
        # Trim to n and shuffle to remove tile seams
        tiled = tiled[:n]
        rng.shuffle(tiled)
        per_dim[dim_name] = tiled

    dims = list(per_dim.keys())
    result: list[dict] = []
    for i in range(n):
        sample = {dim: per_dim[dim][i] for dim in dims}
        result.append(sample)
    return result
